import asyncio
import json
import logging
import sys
import os
import requests
from datetime import datetime, timedelta

# Keep playwright as optional for basic checks if needed, but critical for execution
try:
    from playwright.async_api import async_playwright
except ImportError:
    print("❌ 缺少 playwright 库，请运行: pip install playwright && playwright install chromium")
    sys.exit(1)

# === 配置区域 ===
# 目标 URL: Coinglass 币安合约筛选器页面 (包含所有币种价格和 OI)
TARGET_URL = 'https://www.coinglass.com/zh/exchanges/Binance'

# 历史数据文件 (存储 OI)
HISTORY_FILE = "history_oi.json"
CONFIG_FILE = "config.json"

# 设置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# === 核心注入脚本 (V5.6 Safe Mode - OI Adapted) ===
# 原理: 劫持 JSON.parse，拦截页面加载时的 API 响应数据
INJECT_JS = """
(function() {
    console.log("[JS] Injecting God Mode for OI...");
    const originalParse = JSON.parse;
    JSON.parse = function(text) {
        const result = originalParse.apply(this, arguments);
        try {
            if (text && text.length > 500 && result && typeof result === 'object') {
                detect(result);
            }
        } catch(e) {}
        return result;
    };

    function detect(json) {
        let list = null;
        // 智能尝试解析不同层级的 list
        if (Array.isArray(json)) list = json;
        else if (json.data && Array.isArray(json.data)) list = json.data;
        else if (json.list && Array.isArray(json.list)) list = json.list;
        else if (json.data && json.data.list && Array.isArray(json.data.list)) list = json.data.list;

        if (!list || list.length < 5) return;

        // 特征检测: 必须包含 symbol 和 openInterest
        const first = list[0];
        if (!first || typeof first !== 'object') return;
        const keys = Object.keys(first);
        const hasSymbol = keys.includes('symbol') || keys.includes('uSymbol');
        // 必须含有持仓数据
        const hasOI = keys.includes('openInterest') || keys.includes('oi') || keys.includes('oiAmount');

        if (hasSymbol && hasOI) {
             if (window.onCapturedData) {
                 window.onCapturedData(JSON.stringify(list));
             }
        }
    }
})();
"""

def load_config():
    """读取配置文件"""
    if not os.path.exists(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"⚠️ 读取配置文件失败: {e}")
        return {}

def load_history():
    """读取上次持仓快照"""
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data
    except Exception as e:
        logger.warning(f"⚠️ 读取历史文件失败: {e}")
        return {}

def save_history(current_data):
    """保存当前持仓快照"""
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(current_data, f, indent=2)
        logger.info(f"💾 已保存 {len(current_data)} 个币种的持仓快照")
    except Exception as e:
        logger.error(f"❌ 保存历史文件失败: {e}")

async def run_browser():
    async with async_playwright() as p:
        logger.info("🚀 启动无头浏览器 (Open Interest Monitor)...")
        # 启动 Chromium
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )

        page = await context.new_page()

        # 创建 Future 对象用于等待数据捕获
        data_captured = asyncio.Future()

        # 暴露 Python 函数给 JS 调用
        await page.expose_function("onCapturedData", lambda d: on_data_received(d, data_captured))

        # 注入劫持脚本
        await page.add_init_script(INJECT_JS)

        logger.info(f"👉 正在访问: {TARGET_URL}")
        try:
            # wait_until="networkidle" 确保页面完全加载
            response = await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
            if response.status != 200:
                logger.warning(f"⚠️ 页面返回状态码: {response.status}")
        except Exception as e:
             # logger.warning(f"⚠️ 页面加载提示: {e}")
             pass

        logger.info("⏳ 等待持仓数据捕获...")

        try:
            # 最多等待 50 秒
            raw_data = await asyncio.wait_for(data_captured, timeout=50.0)
            return raw_data
        except asyncio.TimeoutError:
            logger.error("❌ 失败: 50秒内未捕获到有效持仓数据")
            return None
        finally:
            await browser.close()

def on_data_received(json_str, future):
    if not future.done():
        future.set_result(json_str)
        logger.info("✅ 成功捕获持仓数据包!")

def analyze_and_notify(raw_json, config):
    if not raw_json: return

    monitor_cfg = config.get("monitor_settings", {})
    CHANGE_THRESHOLD = monitor_cfg.get("oi_change_threshold", 0.05) # 默认 5%
    # 移除最小持仓限制 (默认为 0)
    MIN_OI_USDT = monitor_cfg.get("min_oi_usdt", 0)
    INTERVAL_SEC = monitor_cfg.get("interval_seconds", 300)

    try:
        data_list = json.loads(raw_json)
        logger.info(f"📊 解析到 {len(data_list)} 条数据")

        # 1. 提取当前 OI 映射
        current_map = {}
        for item in data_list:
            # 兼容字段名
            symbol = item.get('symbol') or item.get('uSymbol')
            # 尝试获取 openInterest
            oi = item.get('openInterest') or item.get('oi')
            price = item.get('price') or item.get('lastPrice') or item.get('close') or 0

            # 必须有 symbol 和 oi
            if symbol and oi is not None:
                # 统一格式
                symbol = symbol.replace('/USDT', '') + 'USDT'
                try:
                    oi_val = float(oi)
                    price_val = float(price)
                    # 简单过滤: OI 太小的忽略，避免噪音
                    # 注意: Coinglass 的 openInterest 单位通常是 币的数量，还是 USDT?
                    # 通常页面上显示的是 USDT 价值，或者需要乘以 price。
                    # API 返回的 openInterest 往往是 "持仓数量" (Coin amount)。
                    # 需要计算 Notion Value = OI * Price

                    # 观察 Coinglass API，通常 openInterest 是 value 还是 amount?
                    # 大多数 API 返回的是 amount。假设我们需要计算 value。
                    # 如果 raw data 里有 'openInterestAmount' (USDT)，则优先用之。
                    # 但假设是 quantity，则 value = oi * price

                    # 修正: Coinglass 网页版 API 通常返回 openInterest (Coin Amount) 和 openInterestAmount (USDT Value)?
                    # 安全起见，存储 { "oi": 123.4, "price": 456, "ts": ... }

                    # 假设 openInterest 是 Quantity
                    # 计算持仓价值 (作为参考数据展示，不再作为过滤条件)
                    oi_usdt = oi_val * price_val if price_val > 0 else 0

                    # 如果 API 直接提供了 openInterestAmount (USDT)
                    if 'openInterestAmount' in item:
                        oi_usdt = float(item['openInterestAmount'])

                    # 仅保留最基本的非零检查
                    if oi_usdt >= MIN_OI_USDT:
                        current_map[symbol] = {
                            "oi": oi_val,
                            "price": price_val,
                            "oi_usdt": oi_usdt,
                            "time": datetime.now().timestamp()
                        }
                except:
                    pass

        # 2. 读取历史
        history_map = load_history()

        # 3. 对比计算异动
        alerts = []
        for symbol, curr_data in current_map.items():
            if symbol not in history_map:
                continue

            last_data = history_map[symbol]
            last_oi = last_data.get('oi', 0)

            if last_oi <= 0: continue

            curr_oi = curr_data['oi']

            # 计算变化率
            change_pct = (curr_oi - last_oi) / last_oi

            if abs(change_pct) >= CHANGE_THRESHOLD:
                trend = "🚀" if change_pct > 0 else "📉"
                alerts.append({
                    "symbol": symbol,
                    "oi": curr_oi,
                    "oi_usdt": curr_data['oi_usdt'],
                    "price": curr_data['price'],
                    "change": change_pct,
                    "trend": trend,
                    "prev_oi": last_oi
                })

        # 4. 保存新历史 (覆盖旧的)
        # 简单全量覆盖
        save_history(current_map)

        # 5. 推送
        if alerts:
            # 按变化幅度排序
            alerts.sort(key=lambda x: abs(x['change']), reverse=True)
            send_feishu(alerts, config)
        else:
            logger.info("🍵 无显著持仓异动 (阈值: {:.1f}%)".format(CHANGE_THRESHOLD * 100))

    except Exception as e:
        logger.error(f"❌ 数据解析错误: {e}")
        import traceback
        traceback.print_exc()

def send_feishu(alerts, config):
    webhook = config.get("feishu_webhook") or os.environ.get("FEISHU_WEBHOOK")
    if not webhook:
        logger.warning("⚠️ 未配置 FEISHU_WEBHOOK，跳过推送")
        for a in alerts[:5]:
            print(f"   {a['trend']} {a['symbol']} OI: {a['change']*100:.2f}% (${a['oi_usdt']/10000:.0f}万)")
        return

    # 构建卡片
    lines = []
    top_alerts = alerts[:20]

    for item in top_alerts:
        symbol = item['symbol'].replace("USDT", "")
        # 格式: 🚀 BTC +5.2% OI: $1.2B
        change_str = f"+{item['change']*100:.2f}%" if item['change'] > 0 else f"{item['change']*100:.2f}%"

        # 格式化 OI 金额
        val = item['oi_usdt']
        if val > 100000000: # 1亿
            oi_str = f"${val/100000000:.2f}亿"
        else:
            oi_str = f"${val/10000:.0f}万"

        # Coinglass K线链接
        link = f"https://www.coinglass.com/tv/Binance_{item['symbol']}"

        line = f"{item['trend']} **[{symbol}]({link})** `{change_str}` <font color='grey'>{oi_str}</font>"
        lines.append(line)

    if len(alerts) > 20:
        lines.append(f"... 还有 {len(alerts)-20} 个异动未显示")

    time_str = (datetime.utcnow() + timedelta(hours=8)).strftime("%H:%M")

    card = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"⚡ 持仓异动监控 [{time_str}]"
                },
                "template": "orange" if alerts[0]['change'] > 0 else "indigo"
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": "\n".join(lines)
                    }
                },
                {
                    "tag": "note",
                    "elements": [{"tag": "plain_text", "content": f"阈值: {config.get('monitor_settings', {}).get('oi_change_threshold', 0.05)*100:.0f}% | 最小持仓: {config.get('monitor_settings', {}).get('min_oi_usdt', 10000000)/10000:.0f}万U"}]
                }
            ]
        }
    }

    try:
        requests.post(webhook, json=card)
        logger.info(f"✅ 已推送 {len(alerts)} 条持仓异动")
    except Exception as e:
        logger.error(f"❌ 推送失败: {e}")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    config = load_config()

    # 打印 GitHub Actions 指引
    if 'GITHUB_ACTIONS' not in os.environ:
        if not config.get("feishu_webhook") and not os.environ.get("FEISHU_WEBHOOK"):
            print("\n[WARN] ⚠️ 本地运行且未配置 feishu_webhook。")
            print("若要部署至 GitHub Actions，请务必在仓库 Settings -> Secrets and variables -> Actions 中添加 'FEISHU_WEBHOOK'。\n")

    raw_data = asyncio.run(run_browser())
    if raw_data:
        analyze_and_notify(raw_data, config)
