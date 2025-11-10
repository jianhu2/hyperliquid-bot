import random
import math
import time
import json
import numpy as np
import example_utils
import ema
from hyperliquid.utils import constants
from datetime import datetime

# =========================
# === 核心策略参数 ===
# =========================
MY_INVESTMENT_USD = 288.66
BASE_SLEEP_SECONDS = 30
RANDOM_SLEEP_MAX = 120

# 币种列表
ALL_COINS = ["ETH", "SOL", "ZEC", "ASTER"]  # 支持的币种
OPEN_ALL_COINS = False  # True = 所有币种开仓，False = 随机选一个币种开仓

# 风控参数
LIQUIDATION_WARNING_PERCENT = 10.0
LIQUIDATION_DANGER_PERCENT = 3.5
AUTO_CLOSE_PERCENT = 1.3
RISK_COOLDOWN_MINUTES = 5

# 盈利止盈参数
FEE_RATIO = 0.011
BASE_MULTIPLE = 8.6
RANDOM_MULTIPLE = 10
PROFIT_CLOSE_COOLDOWN = 60
FUNDING_RATE_BASE = 0.0001

# 亏损止损参数
MAX_LOSS_PERCENT = -0.02
LOSS_CONFIRM_COUNT = 2
WINDOW_SECONDS = 3600
VOL_WINDOW = 10                    # 波动平滑窗口大小（最近10次采样）

# 全局状态
last_risk_close_time = None
last_profit_close_time = None
loss_times = []
vol_history = []
daily_selected_coin = None
daily_date = None

# =========================
# === 工具函数 ===
# =========================
def get_random_sleep():
    return BASE_SLEEP_SECONDS + random.uniform(0, RANDOM_SLEEP_MAX)

def get_random_profit():
    return BASE_MULTIPLE + random.uniform(0, RANDOM_MULTIPLE)

def get_position_info(user_state, coin_name):
    for p in user_state.get("assetPositions", []):
        pos = p.get("position", {})
        if pos.get("coin") == coin_name:
            return pos
    return None

def get_accurate_liquidation_price(user_state, coin_name, current_price):
    try:
        for p in user_state.get("assetPositions", []):
            pos = p.get("position", {})
            if pos.get("coin") == coin_name:
                if "liquidationPx" in pos:
                    return float(pos["liquidationPx"])
                lev = float(pos.get("leverage", {}).get("value", 1))
                szi = float(pos.get("szi", 0))
                if szi > 0:
                    return current_price * (1 - 0.95 / lev)
                elif szi < 0:
                    return current_price * (1 + 0.95 / lev)
        return None
    except Exception as e:
        print(f"⚠️ 获取清算价格失败: {e}")
        return None

def calculate_safety_margin(current_price, liquidation_price, is_long):
    if not liquidation_price or liquidation_price <= 0:
        return None
    try:
        if is_long:
            return max(((current_price - liquidation_price) / current_price) * 100, 0)
        else:
            return max(((liquidation_price - current_price) / current_price) * 100, 0)
    except:
        return None

def get_risk_level(safety_margin):
    if safety_margin is None:
        return "未知", "⚪"
    if safety_margin >= LIQUIDATION_WARNING_PERCENT:
        return "非常安全", "🟢"
    elif safety_margin >= LIQUIDATION_DANGER_PERCENT:
        return "安全", "🟡"
    elif safety_margin >= AUTO_CLOSE_PERCENT:
        return "警告", "🟠"
    else:
        return "极度危险", "💀"

def should_trigger_risk_management(safety_margin):
    return safety_margin is not None and safety_margin < LIQUIDATION_WARNING_PERCENT

def execute_risk_management(exchange, coin, safety_margin, risk_level, current_price, liquidation_price):
    global last_risk_close_time
    print(f"🚨 风控触发！安全边际: {safety_margin:.1f}% ({risk_level})")
    if safety_margin <= AUTO_CLOSE_PERCENT:
        print(f"💥 安全边际过低({safety_margin:.1f}%) -> 紧急平仓")
        close_result = exchange.market_close(coin)
        print(f"✅ 平仓结果: {json.dumps(close_result)}")
        last_risk_close_time = time.time()
        sleep_time = get_random_sleep()
        print(f"⏳ 风控平仓后等待 {sleep_time:.1f}s")
        time.sleep(sleep_time)
        return "closed"
    return "warning"

def should_reopen_after_risk_close():
    global last_risk_close_time
    if last_risk_close_time is None:
        return True
    cooldown = RISK_COOLDOWN_MINUTES * 60
    elapsed = time.time() - last_risk_close_time
    if elapsed < cooldown:
        remain = cooldown - elapsed
        print(f"⏳ 风控冷却中: {int(remain // 60)}分{int(remain % 60)}秒")
        return False
    last_risk_close_time = None
    return True

def calculate_gross_roe(my_pos, current_price):
    if not my_pos:
        return 0.0
    try:
        roe = my_pos.get("returnOnEquity")
        if roe is not None:
            return float(roe)
    except:
        pass
    try:
        entry_px = float(my_pos.get("entryPx") or my_pos.get("avgEntryPrice") or my_pos.get("entryPrice") or 0)
        if entry_px <= 0:
            return 0.0
        is_long = float(my_pos.get("szi", 0)) > 0
        return (current_price - entry_px) / entry_px if is_long else (entry_px - current_price) / entry_px
    except:
        return 0.0

def calculate_holding_fee(my_pos):
    if not my_pos:
        return 0.0
    try:
        cum = my_pos.get("cumFunding", {})
        since_open = cum.get("sinceOpen")
        if since_open is not None:
            return float(since_open)
    except:
        pass
    try:
        open_time = float(my_pos.get("openTime", time.time()))
        hours_held = (time.time() - open_time) / 3600
        leverage = int(my_pos.get("leverage", {}).get("value", 1))
        return FUNDING_RATE_BASE * hours_held * leverage
    except:
        return 0.0

def should_reopen_after_profit_close():
    global last_profit_close_time
    if last_profit_close_time is None:
        return True
    elapsed = time.time() - last_profit_close_time
    if elapsed >= PROFIT_CLOSE_COOLDOWN:
        last_profit_close_time = None
        return True
    remain = PROFIT_CLOSE_COOLDOWN - elapsed
    print(f"⏳ 盈利平仓冷却中: {int(remain)}秒后才能重新开仓")
    return False


def should_stop_loss(my_pos, current_price, my_lev, volatility):
    """
    更稳健的动态止损判断函数（带平滑波动检测）
    """

    # === Step 1: 计算净浮动盈亏 ===
    entry_price = float(my_pos.get("entryPx") or my_pos.get("avgEntryPrice") or 0)
    if entry_price == 0:
        return False

    my_is_long = float(my_pos.get("szi", 0)) > 0
    net_profit = (current_price / entry_price - 1) * (1 if my_is_long else -1)

    # === Step 2: 平滑波动率 ===
    vol_history.append(volatility)
    if len(vol_history) > 50: 
       vol_history.pop(0)
    smooth_vol = np.mean(vol_history) if vol_history else volatility

    # === Step 3: 动态止损阈值 ===
    dyn_stop_loss = MAX_LOSS_PERCENT * min(my_lev / 10, 2.0) / max(1.0, smooth_vol / 0.006)

    print(f"动态止损 net_profit={net_profit:.6f},stop_loss_profit={dyn_stop_loss:.6f}")
    # === Step 4: 检测是否触发止损 ===
    if net_profit <= dyn_stop_loss:
        loss_times.append(time.time())
        # 在时间窗口内统计触发次数
        now = time.time()
        loss_in_window = [t for t in loss_times if now - t <= WINDOW_SECONDS]
        print(f"⚠️ 止损检测: net={net_profit:.4f}, dyn={dyn_stop_loss:.4f}, 次数={len(loss_in_window)}")

        if len(loss_in_window) >= LOSS_CONFIRM_COUNT:
            # 二次确认：连续亏损 + 波动上升
            if smooth_vol > 0.006:
                print(f"💥 连续 {LOSS_CONFIRM_COUNT} 次止损触发 + 高波动({smooth_vol:.4f}) → 执行止损！")
                loss_times.clear()
                return True
            else:
                print(f"📊 波动率较低({smooth_vol:.4f})，暂缓止损确认。")
    else:
        # 盈利或回撤修复，自动清零触发计数
        loss_times.clear()

    return False

# =========================
# === 仓位处理函数 ===
# =========================
def handle_position(exchange, coin, my_pos, current_price, info):
    """处理已有仓位：风控/止盈/EMA反向平仓"""
    global loss_times, last_profit_close_time

    my_is_long = float(my_pos.get("szi", 0)) > 0
    my_lev = int(my_pos.get("leverage", {}).get("value", 1))
    my_sz = abs(float(my_pos.get("szi", 0)))
    entry_price = float(my_pos.get("entryPx") or my_pos.get("avgEntryPrice") or my_pos.get("entryPrice") or 0.0)

    liq_px = get_accurate_liquidation_price(info.user_state(my_address), coin, current_price)
    margin = calculate_safety_margin(current_price, liq_px, my_is_long)
    level, emoji = get_risk_level(margin)

       # 计算短期波动率
    closes = ema.get_kline_data(info, coin, "15m")
    volatility = ema.calculate_volatility(closes)

    print(f"📊 我的仓位:${entry_price} {'多单' if my_is_long else '空单'} {my_sz:.4f} {coin} ({my_lev}x)")
    if liq_px:
        print(f"📉 当前价：${current_price:.2f} | 清算价: ${liq_px:.2f} | 安全边际: {margin:.1f}% {emoji} {level} | 波动率：{volatility:.4f}")

    # 风控平仓
    if should_trigger_risk_management(margin):
        act = execute_risk_management(exchange, coin, margin, level, current_price, liq_px)
        if act == "closed":
            return True

    gross_roe = calculate_gross_roe(my_pos, current_price)
    holding_fee = calculate_holding_fee(my_pos)
    total_fee = FEE_RATIO + holding_fee
    net_profit = gross_roe - holding_fee


    # EMA趋势反向平仓
    trend = ema.get_ema_trend(info, coin,"15m")
    if trend:
        if my_is_long and trend == "SHORT":
            print(f"🔄 EMA反向平仓触发，多单 -> 空单趋势")
            exchange.market_close(coin)
            last_profit_close_time = time.time()
            time.sleep(get_random_sleep())
            return True
        elif not my_is_long and trend == "LONG":
            print(f"🔄 EMA反向平仓触发，空单 -> 多单趋势")
            exchange.market_close(coin)
            last_profit_close_time = time.time()
            time.sleep(get_random_sleep())
            return True

    # 盈利止盈
    PROFIT_MULTIPLE = get_random_profit()
    close_profit = PROFIT_MULTIPLE * total_fee
    print(f"动态止盈 net_profit={net_profit:.6f},close_profit={close_profit:.6f}")
    if net_profit >= close_profit:
        print(f"💹 盈利止盈触发 net_profit={net_profit:.6f}")
        exchange.market_close(coin)
        last_profit_close_time = time.time()
        time.sleep(get_random_sleep())
        return True

    # === 调用止损逻辑 ===
    if should_stop_loss(my_pos, current_price, my_lev, volatility):
        exchange.market_close(coin)
        last_profit_close_time = time.time()
        time.sleep(get_random_sleep())
        return True

    return False

# =========================
# === 开仓函数 ===
# =========================
def open_position(exchange, coin, current_price, trend):
    """趋势内随机入场"""
    if random.random() > 0.3:
        print("🎲 随机未触发入场，等待下一轮")
        return
    sz = math.floor((MY_INVESTMENT_USD / current_price) / 0.01) * 0.01
    if sz * current_price < 10:
        print(f"⚠️ 开仓规模过小: {sz*current_price:.2f} USD，跳过")
        return
    is_long = (trend == "LONG")
    lev = random.choice([5, 10, 15, 20, 25])
    exchange.update_leverage(lev, coin)
    exchange.market_open(coin, is_long, sz, None, 0.01)
    print(f"✅ 新开仓: {'多单' if is_long else '空单'}, 数量={sz:.8f}, 杠杆={lev}x, 价格={current_price}")

# =========================
# === 主循环 ===
# =========================

def select_coins():
    """随机选择一次开仓币种"""
    global daily_selected_coin, daily_date
    today = datetime.now().date()
    if OPEN_ALL_COINS:
        return ALL_COINS
    if daily_date != today or daily_selected_coin is None:
        daily_selected_coin = random.choice(ALL_COINS)
        daily_date = today
        print(f"🎲 今天随机选择开仓币种: {daily_selected_coin}")
    return [daily_selected_coin]


def main_multi_coin():
    global my_address, last_risk_close_time, last_profit_close_time

    # 初始化
    my_address, info, exchange = example_utils.setup(base_url=constants.MAINNET_API_URL)
    print(f"--- EMA顺势+反向平仓+止盈止损策略 ---\n地址: {my_address}\n币种列表: {ALL_COINS}\n模式: {'全开' if OPEN_ALL_COINS else '随机开一个'}")

    try:
        while True:
            print(f"\n🕒 {time.strftime('%Y-%m-%d %H:%M:%S')} 获取行情...")
            all_mids = info.all_mids()

            # 选择本轮要开仓的币种
            coins_to_open = select_coins()

            for coin in ALL_COINS:
                current_price = float(all_mids.get(coin, 0))
                if current_price == 0:
                    print(f"❌ 获取价格失败: {coin}")
                    continue

                my_pos = get_position_info(info.user_state(my_address), coin)

                # 如果该币种不在本轮开仓列表，且有仓位，先平仓
                if coin not in coins_to_open and my_pos:
                    print(f"⚠️  {coin} 不在本轮开仓列表，先平仓")
                    exchange.market_close(coin)
                    continue

                # 处理已有仓位
                if my_pos:
                    handled = handle_position(exchange, coin, my_pos, current_price, info)
                    if handled:
                        continue

               # 开仓逻辑
                else:
                  if coin in coins_to_open:
                     if should_reopen_after_profit_close() and should_reopen_after_risk_close():
                        trend = ema.get_ema_trend(info, coin)
                        if trend:
                            open_position(exchange, coin, current_price, trend)
                        else:
                            print(f"⏸️  {coin} 趋势不明确，暂不开仓")

            time.sleep(BASE_SLEEP_SECONDS)

    except KeyboardInterrupt:
        print("\n🛑 手动中断，安全退出")
    except Exception as e:
        import traceback
        print(f"\n❌ 未知错误: {e}")
        traceback.print_exc()
    finally:
        print("程序已退出。")



if __name__ == "__main__":
    main_multi_coin()

