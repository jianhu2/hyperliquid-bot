import random
import math
import time
import json
import example_utils
from hyperliquid.utils import constants

# --- 核心配置参数 ---
MY_INVESTMENT_USD = 288.66
COIN = "ETH"
BASE_SLEEP_SECONDS = 30   # 基础等待时间
RANDOM_SLEEP_MAX = 120     # 最大随机浮动时间（秒）

# 风险控制参数
LIQUIDATION_WARNING_PERCENT = 10.0
LIQUIDATION_DANGER_PERCENT = 3.5
AUTO_CLOSE_PERCENT = 1.3
RISK_COOLDOWN_MINUTES = 5

# 盈利止盈参数
FEE_RATIO = 0.01          # 开仓+平仓总手续费
BASE_MULTIPLE = 2.5       # 基础收益率达到止盈倍数
RANDOM_MULTIPLE = 2       # 随机止盈倍数    ((基础倍数+随机止盈倍数) * 总费用) 
PROFIT_CLOSE_COOLDOWN = 60  # 平仓后冷却期(秒)
FUNDING_RATE_BASE = 0.0001  # 基础持仓费比例（每小时估算，fallback 用）

# 亏损止损阈值 (例如 -3% ROE)
MAX_LOSS_PERCENT = -0.05  
LOSS_CONFIRM_COUNT = 3   # 3次检查到低于止损阈值就立即执行止损，防止假信号
WINDOW_SECONDS = 3600      # 止损时间窗口: 在1小时以内3次检查到低于止损阈值就立即执行止损

# 全局状态
last_risk_close_time = None
last_profit_close_time = None
loss_times = []


# ----------------------
# 工具函数
# ----------------------
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
    # fallback
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
    # fallback估算
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

# ----------------------
# 主循环
# ----------------------
def main():
    global last_risk_close_time, last_profit_close_time
    my_address, info, exchange = example_utils.setup(base_url=constants.MAINNET_API_URL)
    print(f"--- 单币随机开平仓机器人 ---\n我的地址: {my_address}\n交易币种: {COIN}")

    try:
        while True:
            print(f"\n🕒 {time.strftime('%Y-%m-%d %H:%M:%S')} 获取行情...")
            all_mids = info.all_mids()
            my_state = info.user_state(my_address)
            current_price = float(all_mids.get(COIN, 0))
            if current_price == 0:
                print("❌ 获取价格失败")
                time.sleep(get_random_sleep())
                continue

            my_pos = get_position_info(my_state, COIN)
            if last_risk_close_time and not should_reopen_after_risk_close():
                time.sleep(get_random_sleep())
                continue

            # --- 如果有仓位，先处理风控、止盈、止损 ---
            if my_pos:
                my_is_long = float(my_pos["szi"]) > 0
                my_lev = int(my_pos.get("leverage", {}).get("value", 1))
                my_sz = abs(float(my_pos["szi"]))
                entry_price = float(my_pos.get("entryPx") or my_pos.get("avgEntryPrice") or my_pos.get("entryPrice") or 0.0)
                liq_px = get_accurate_liquidation_price(my_state, COIN, current_price)
                margin = calculate_safety_margin(current_price, liq_px, my_is_long)
                level, emoji = get_risk_level(margin)
                print(f"📊 我的仓位:${entry_price} {'多单' if my_is_long else '空单'} {my_sz:.4f} {COIN} ({my_lev}x)")
                if liq_px:
                    print(f"📉 当前价：${current_price:.2f} | 清算价: ${liq_px:.2f} | 安全边际: {margin:.1f}% {emoji} {level}")

                # 风控平仓
                if should_trigger_risk_management(margin):
                    act = execute_risk_management(exchange, COIN, margin, level, current_price, liq_px)
                    if act == "closed":
                        continue

                gross_roe = calculate_gross_roe(my_pos, current_price)
                holding_fee = calculate_holding_fee(my_pos)
                total_fee = FEE_RATIO + holding_fee
                net_profit = gross_roe - holding_fee


                # 盈利止盈
                PROFIT_MULTIPLE = get_random_profit()               
                print(f"🔎 gross_roe={gross_roe:.6f}, holding_fee={holding_fee:.6f}, total_fee={total_fee:.6f}, close_profit={PROFIT_MULTIPLE * total_fee:.6f}")
                if net_profit >= PROFIT_MULTIPLE * total_fee:
                    print(f"💹 盈利止盈触发 net_profit={net_profit:.6f}")
                    exchange.market_close(COIN)
                    last_profit_close_time = time.time()
                    sleep_time = get_random_sleep()
                    print(f"⏳ 平仓后等待 {sleep_time:.1f}s 再继续")
                    time.sleep(sleep_time)
                    continue

                # 亏损止损
                if net_profit <= MAX_LOSS_PERCENT:
                     if 'loss_times' not in locals():
                         loss_times = []
                    # 添加当前时间戳
                     now = time.time()
                     loss_times.append(now)
                    # 清理1小时以外的记录
                     loss_times = [t for t in loss_times if now - t <= WINDOW_SECONDS]
                     print(f"⚠️ 风控触发计数: {len(loss_times)}/{LOSS_CONFIRM_COUNT} 在1小时内")
                     if len(loss_times) >= LOSS_CONFIRM_COUNT:
                        print("💥 1小时内连续3次亏损，执行止损！")
                        loss_times = []  # 重置计数
                        print(f"⚠️ 亏损止损触发 net_profit={net_profit:.6f}")
                        exchange.market_close(COIN)
                        last_profit_close_time = time.time()
                        loss_counter = 0
                        sleep_time = get_random_sleep()
                        print(f"⏳ 平仓后等待 {sleep_time:.1f}s 再继续")
                        time.sleep(sleep_time)
                        continue

            # --- 开仓逻辑 ---
            if my_pos is None and should_reopen_after_profit_close():
                sz = math.floor((MY_INVESTMENT_USD / current_price) / 0.01) * 0.01
                if sz * current_price < 10:
                    print(f"⚠️ 开仓规模过小: {sz*current_price:.2f} USD，跳过")
                    time.sleep(get_random_sleep())
                    continue
                # 随机多空
                is_long = random.choice([True, False])
                lev = random.choice([5, 10, 25])
                exchange.update_leverage(lev, COIN)
                order = exchange.market_open(COIN, is_long, sz, None, 0.01)
                print(f"✅ 新开仓: {'多单' if is_long else '空单'}, 数量={sz:.8f}, 杠杆={lev}x, 价格={current_price}")
                time.sleep(BASE_SLEEP_SECONDS)
                continue        
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
    main()



