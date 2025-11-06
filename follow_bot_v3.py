import time
import json
import example_utils
from hyperliquid.utils import constants

# --- 核心配置参数 ---
TARGET_USER_ADDRESS = "0x9263c1bd29aa87a118242f3fbba4517037f8cc7a"
MY_INVESTMENT_USD = 168.88
TAKE_PROFIT_USD = 4500.0
COIN = "ETH"
LOOP_SLEEP_SECONDS = 30

# --- 风险控制参数 ---
LIQUIDATION_WARNING_PERCENT = 10.0
LIQUIDATION_DANGER_PERCENT = 3.5
AUTO_CLOSE_PERCENT = 2.0
RISK_COOLDOWN_MINUTES = 5

# --- 全局状态 ---
last_risk_close_time = None


def get_position_info(user_state, coin_name):
    """从用户状态中提取特定币种的持仓信息"""
    for p in user_state.get("assetPositions", []):
        pos = p.get("position", {})
        if pos.get("coin") == coin_name:
            return pos
    return None


def get_accurate_liquidation_price(user_state, coin_name, current_price):
    """从用户状态中获取准确的清算价格"""
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
    """计算安全边际百分比"""
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
    """风险等级评估"""
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
    """风险管理"""
    global last_risk_close_time

    print(f"🚨 风控触发！安全边际: {safety_margin:.1f}% ({risk_level})")

    if safety_margin <= AUTO_CLOSE_PERCENT:
        print(f"💥 安全边际过低({safety_margin:.1f}%) -> 紧急平仓")
        close_result = exchange.market_close(coin)
        print(f"✅ 平仓结果: {json.dumps(close_result)}")
        last_risk_close_time = time.time()
        return "closed"
    return "warning"


def should_reopen_after_risk_close():
    """检查冷却期"""
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


def main():
    global last_risk_close_time
    my_address, info, exchange = example_utils.setup(base_url=constants.MAINNET_API_URL)

    print("--- 跟单机器人 V3 (持仓同步 + 实时风险提示) ---")
    print(f"跟随地址: {TARGET_USER_ADDRESS}\n我的地址: {my_address}\n目标币种: {COIN}")
    print("-------------------------------------------------------")

    try:
        while True:
            print(f"\n🕒 {time.strftime('%Y-%m-%d %H:%M:%S')} 获取最新行情...")

            all_mids = info.all_mids()
            target_state = info.user_state(TARGET_USER_ADDRESS)
            my_state = info.user_state(my_address)

            current_price = float(all_mids.get(COIN, 0))
            if current_price == 0:
                print("❌ 获取价格失败")
                time.sleep(LOOP_SLEEP_SECONDS)
                continue

            target_pos = get_position_info(target_state, COIN)
            my_pos = get_position_info(my_state, COIN)

            # 🆕 每轮循环打印当前价格
            print(f"💰 {COIN} 当前价格: ${current_price:.2f}")

            # --- 冷却状态检查 ---
            if last_risk_close_time and not should_reopen_after_risk_close():
                time.sleep(LOOP_SLEEP_SECONDS)
                continue

            # --- 目标无持仓 ---
            if not target_pos:
                print("🟡 目标账户无持仓")
                if my_pos:
                    print("🔻 自身仍有仓位，执行平仓")
                    result = exchange.market_close(COIN)
                    print(f"平仓结果: {json.dumps(result)}")
                time.sleep(LOOP_SLEEP_SECONDS)
                continue

            # --- 提取目标方向 ---
            target_is_long = float(target_pos["szi"]) > 0
            target_lev = int(target_pos["leverage"]["value"])
            target_size = abs(float(target_pos["szi"]))
            print(f"🎯 目标方向: {'多单' if target_is_long else '空单'} "
                  f"{target_size:.4f} {COIN} ({target_lev}x)")

            # --- 自身无持仓 => 跟随开仓 ---
            if my_pos is None:
                sz = round(MY_INVESTMENT_USD / current_price, 5)
                exchange.update_leverage(target_lev, COIN)
                order = exchange.market_open(COIN, target_is_long, sz, None, 0.01)
                print(f"✅ 跟随开仓完成: {json.dumps(order)}")

            else:
                my_is_long = float(my_pos["szi"]) > 0
                my_lev = int(my_pos["leverage"]["value"])
                my_sz = abs(float(my_pos["szi"]))
                my_value = my_sz * current_price

                liq_px = get_accurate_liquidation_price(my_state, COIN, current_price)
                margin = calculate_safety_margin(current_price, liq_px, my_is_long)
                level, emoji = get_risk_level(margin)

                print(f"📊 我的仓位: {'多单' if my_is_long else '空单'} {my_sz:.4f} {COIN} ({my_lev}x)")
                if liq_px:
                    print(f"📉 清算价: ${liq_px:.2f} | 安全边际: {margin:.1f}% {emoji} {level}")

                # 🆕 打印当前状态即便无风险
                if not should_trigger_risk_management(margin):
                    print("✅ 风险正常")
                else:
                    act = execute_risk_management(exchange, COIN, margin, level, current_price, liq_px)
                    if act == "closed":
                        time.sleep(LOOP_SLEEP_SECONDS)
                        continue

                # --- 🆕 持仓方向不一致时自动调整 ---
                if my_is_long != target_is_long:
                    print(f"⚠️ 持仓方向不一致 -> 平掉当前仓位并调整方向")
                    exchange.market_close(COIN)
                    exchange.update_leverage(target_lev, COIN)
                    new_sz = round(MY_INVESTMENT_USD / current_price, 5)
                    order = exchange.market_open(COIN, target_is_long, new_sz, None, 0.01)
                    print(f"🔁 仓位调整完成: {json.dumps(order)}")

                # --- 达到止盈 ---
                if my_value >= TAKE_PROFIT_USD:
                    print(f"🎉 达到止盈 (${my_value:.2f} ≥ ${TAKE_PROFIT_USD}) -> 平仓退出")
                    res = exchange.market_close(COIN)
                    print(f"平仓结果: {json.dumps(res)}")
                    break

            print(f"⏳ 等待 {LOOP_SLEEP_SECONDS}s 后继续监控...")
            time.sleep(LOOP_SLEEP_SECONDS)

    except KeyboardInterrupt:
        print("\n🛑 检测到手动中断，安全退出")
    except Exception as e:
        import traceback
        print(f"\n❌ 未知错误: {e}")
        traceback.print_exc()
    finally:
        print("程序已退出。")


if __name__ == "__main__":
    main()

