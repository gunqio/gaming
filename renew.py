import os, re, time, json, random, logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── 配置 ─────────────────────────────────────────────
# 你的 gaming4free server 页面 URL，格式如 https://g4f.gg/my-mc-server
SERVER_URL = os.environ["G4F_URL"]

WXPUSHER_TOKEN = os.environ.get("APP_TOKEN", "")
WXPUSHER_UID   = os.environ.get("WX_PUSHER_UID", "")

SCREENSHOT_DIR = Path("./screenshots")
SCREENSHOT_DIR.mkdir(exist_ok=True)

# ── WxPusher ─────────────────────────────────────────
def wxpush(content: str):
    if not WXPUSHER_TOKEN or not WXPUSHER_UID:
        log.warning("WxPusher 未配置，跳过推送")
        return
    import urllib.request
    payload = json.dumps({
        "appToken": WXPUSHER_TOKEN,
        "content":  content,
        "contentType": 1,
        "uids": [WXPUSHER_UID],
    }).encode()
    try:
        req = urllib.request.Request(
            "https://wxpusher.zjiecode.com/api/send/message",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("success"):
                log.info("📨 WxPusher 推送成功")
            else:
                log.warning(f"📨 WxPusher 失败: {result}")
    except Exception as e:
        log.warning(f"📨 WxPusher 异常: {e}")

# ── 工具 ─────────────────────────────────────────────
def take_screenshot(page, name: str):
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = str(SCREENSHOT_DIR / f"{ts}_{name}.png")
        page.screenshot(path=path, full_page=False)
        log.info(f"📸 截图: {path}")
    except Exception as e:
        log.warning(f"截图失败: {e}")

def get_text(page) -> str:
    try:
        return page.inner_text("body") or ""
    except:
        return ""

def human_move_and_click(page, element):
    """用 CloakBrowser 人类鼠标轨迹点击元素"""
    try:
        box = element.bounding_box()
        if not box:
            log.warning("元素 bounding_box 为空，fallback JS click")
            element.evaluate("el => el.click()")
            return
        # 在元素中心附近随机落点（±20% 抖动）
        cx = box["x"] + box["width"]  * (0.4 + random.random() * 0.2)
        cy = box["y"] + box["height"] * (0.4 + random.random() * 0.2)
        log.info(f"🖱️  human_move_and_click → ({cx:.0f}, {cy:.0f})")
        page.mouse.move(cx, cy)
        time.sleep(random.uniform(0.08, 0.18))
        page.mouse.click(cx, cy)
    except Exception as e:
        log.warning(f"human_move_and_click 失败，JS fallback: {e}")
        try:
            element.evaluate("el => el.click()")
        except:
            pass

# ── 读取当前剩余时间 ─────────────────────────────────
def read_remaining(page) -> str | None:
    """读取页面上 'SERVER TIME REMAINING' 下方的 HH:MM:SS"""
    try:
        el = page.locator(".timer, [class*='timer'], [class*='countdown']").first
        txt = el.inner_text(timeout=3000).strip()
        if re.match(r'\d+:\d{2}:\d{2}', txt):
            return txt
    except:
        pass
    # 正则从全文找
    body = get_text(page)
    m = re.search(r'(\d{1,3}:\d{2}:\d{2})', body)
    return m.group(1) if m else None

def read_expiry_utc(page) -> str | None:
    """读取 'expires Jun 9, 2026 at 00:33 UTC' 字样"""
    body = get_text(page)
    m = re.search(r'expires\s+(.+?UTC)', body)
    return m.group(1).strip() if m else None

# ── 点击 +ADD 90 MIN 按钮 ────────────────────────────
def click_add_90_btn(page) -> bool:
    """找到并点击 '+ ADD 90 MIN' 按钮，触发 CF 验证弹窗"""
    # 先等待按钮出现
    selectors = [
        "button.vote-btn",
        "button[onclick*='openCaptchaModal']",
        "button:has-text('ADD 90 MIN')",
        ".vote-btn",
    ]
    btn = None
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=4000):
                btn = loc
                log.info(f"✅ 找到 ADD 90 MIN 按钮: {sel}")
                break
        except:
            pass

    if btn is None:
        log.error("找不到 '+ ADD 90 MIN' 按钮")
        take_screenshot(page, "btn_not_found")
        return False

    human_move_and_click(page, btn)
    log.info("已点击 '+ ADD 90 MIN'，等待 CF 验证弹窗...")
    return True

# ── 处理 Cloudflare Turnstile 弹窗（Zytrano 同款坐标点击逻辑）──
def handle_cf_turnstile(page, timeout=30) -> bool:
    """
    gaming4free 验证链路：
      #captcha-modal → iframe (challenges.cloudflare.com) → closed shadow-root → checkbox
    关键：不用选择器穿透 shadow-root，直接用
      cf_frame.frame_element().bounding_box() 拿 iframe 在 page 坐标系的位置，
      然后 page.mouse.click(x+25, y+h/2) 点左侧 checkbox 区域。
    """

    def dump_frames(label: str):
        try:
            frames = page.frames
            log.info(f"[{label}] 共 {len(frames)} 个 frame：")
            for i, f in enumerate(frames):
                log.info(f"  [{i}] {(f.url or 'about:blank')[:120]}")
        except Exception as e:
            log.warning(f"[{label}] dump_frames 失败: {e}")

    # ── 等弹窗出现 ───────────────────────────────────────────
    log.info("等待 #captcha-modal 出现...")
    try:
        page.wait_for_selector("#captcha-modal", state="visible", timeout=20000)
        log.info("✅ #captcha-modal 出现")
    except Exception as e:
        log.error(f"captcha-modal 未出现: {e}")
        take_screenshot(page, "modal_not_found")
        return False

    take_screenshot(page, "cf_modal_appeared")

    # ── 阶段1：等静默通过（最多 10s）──────────────────────────
    log.info("【阶段1】等待 Turnstile 静默通过...")
    for i in range(20):
        if "minutes added" in get_text(page).lower():
            log.info(f"✅ 静默通过（{i * 0.5:.1f}s）")
            return True
        time.sleep(0.5)

    # ── 阶段2：枚举 frames 找 CF iframe（最多 8s）──────────────
    log.info("【阶段2】枚举 frames 查找 Turnstile iframe...")
    dump_frames("阶段2")
    cf_frame = None
    for tick in range(16):
        for f in page.frames:
            if "challenges.cloudflare.com" in (f.url or ""):
                cf_frame = f
                break
        if cf_frame:
            log.info(f"✅ 找到 CF frame（{tick * 0.5:.1f}s）: {cf_frame.url[:100]}")
            break
        time.sleep(0.5)

    if cf_frame is None:
        log.warning("【阶段2】未找到 CF frame，降级：用 iframe src 坐标点击")
        dump_frames("降级")
        take_screenshot(page, "no_cf_frame")
        try:
            iframe_el = page.locator('iframe[src*="challenges.cloudflare.com"]').first
            box = iframe_el.bounding_box()
            log.info(f"  降级 iframe bounding_box={box}")
            if box:
                x = box["x"] + 25
                y = box["y"] + box["height"] / 2
                page.mouse.move(x, y)
                time.sleep(random.uniform(0.2, 0.4))
                page.mouse.click(x, y)
                log.info(f"  ✅ 降级坐标点击 ({x:.0f}, {y:.0f})")
            else:
                log.error("  降级 bounding_box 为 None")
                return False
        except Exception as fe:
            log.error(f"  降级点击失败: {fe}")
            return False
    else:
        # ── 阶段3：frame_element().bounding_box() → page.mouse.click ──
        time.sleep(1)  # 等 iframe 内部 JS 初始化
        log.info("【阶段3】通过 frame_element bounding_box 坐标点击 checkbox...")
        try:
            frame_el = cf_frame.frame_element()
            box = frame_el.bounding_box()
            log.info(f"  frame bounding_box={box}")
            if box:
                x = box["x"] + 25                   # checkbox 在 iframe 左侧 ~25px
                y = box["y"] + box["height"] / 2
                page.mouse.move(x, y)
                time.sleep(random.uniform(0.15, 0.35))
                page.mouse.click(x, y)
                log.info(f"  ✅ 坐标点击 ({x:.0f}, {y:.0f})")
            else:
                log.error("  bounding_box 为 None，iframe 可能不可见")
                take_screenshot(page, "frame_bbox_none")
                return False
        except Exception as e:
            log.error(f"  坐标点击失败: {e}")
            take_screenshot(page, "frame_click_error")
            return False

    take_screenshot(page, "cf_after_click")

    # ── 阶段4：等待成功（最多 30s）────────────────────────────
    # 验证通过后表单自动 POST 提交，页面刷新，banner 出现，
    # 此时 cf-turnstile-response input 已消失，不能用 token 判断。
    # 改为：检测 "minutes added" banner 或 modal 消失二选一。
    log.info("【阶段4】等待续期成功（banner 出现 或 modal 消失）...")
    for i in range(timeout * 2):
        try:
            body = get_text(page)
            if "minutes added" in body.lower():
                log.info(f"✅ 续期成功 banner 出现（{i * 0.5:.1f}s）")
                take_screenshot(page, "cf_success")
                return True
            # modal 消失也视为通过（表单已提交）
            modal_visible = page.locator("#captcha-modal").is_visible(timeout=200)
            if not modal_visible:
                log.info(f"✅ captcha-modal 已消失（{i * 0.5:.1f}s），视为成功")
                take_screenshot(page, "cf_success")
                return True
        except Exception:
            # locator 抛异常说明 modal 已不在 DOM，表单已提交
            log.info(f"✅ modal 已从 DOM 移除（{i * 0.5:.1f}s）")
            take_screenshot(page, "cf_success")
            return True

        if i % 10 == 0 and i > 0:
            log.info(f"  等待中... {i * 0.5:.0f}s")
            take_screenshot(page, f"cf_wait_{i}")
        time.sleep(0.5)

    log.error("【阶段4】等待超时（30s）")
    take_screenshot(page, "cf_timeout")
    return False

# ── 主流程 ───────────────────────────────────────────
def add_90_min(page) -> bool:
    """完整执行一次 +90 min 流程，返回是否成功"""
    # 1. 打开页面
    log.info(f"打开页面: {SERVER_URL}")
    try:
        page.goto(SERVER_URL, timeout=30000, wait_until="domcontentloaded")
    except Exception as e:
        log.warning(f"goto 超时: {e}")
    time.sleep(2)
    take_screenshot(page, "01_page_loaded")

    # 读取续期前的到期时间
    before_expiry = read_expiry_utc(page)
    before_remain = read_remaining(page)
    log.info(f"续期前 → 剩余: {before_remain}, 到期: {before_expiry}")

    # 2. 点击 +ADD 90 MIN
    if not click_add_90_btn(page):
        return False
    time.sleep(2)

    # 3. 处理 CF Turnstile（token 写入即视为验证通过，表单会自动提交）
    if not handle_cf_turnstile(page):
        return False

    # 4. 等待成功 banner
    log.info("等待成功 banner...")
    success = False
    for i in range(20):
        body = get_text(page)
        if "minutes added" in body.lower():
            log.info(f"✅ 成功 banner 出现（{i}s）")
            success = True
            break
        time.sleep(1)

    take_screenshot(page, "02_after_renew")

    after_expiry = read_expiry_utc(page)
    after_remain = read_remaining(page)
    log.info(f"续期后 → 剩余: {after_remain}, 到期: {after_expiry}")

    return success or (after_expiry and after_expiry != before_expiry)

def main():
    from cloakbrowser import launch

    log.info("启动 CloakBrowser...")
    browser = launch(
        headless=False,
        humanize=True,
    )
    page = browser.new_page()
    page.set_viewport_size({"width": 1280, "height": 800})

    # 读取续期前信息（先导航一次）
    before_expiry = None
    before_remain = None
    after_expiry  = None
    after_remain  = None
    success = False

    try:
        # 先访问一次读当前时间
        try:
            page.goto(SERVER_URL, timeout=30000, wait_until="domcontentloaded")
            time.sleep(2)
            before_expiry = read_expiry_utc(page)
            before_remain = read_remaining(page)
            log.info(f"当前状态 → 剩余: {before_remain}, 到期: {before_expiry}")
        except Exception as e:
            log.warning(f"初次读取失败: {e}")

        success = add_90_min(page)

        if success:
            time.sleep(3)
            after_expiry = read_expiry_utc(page)
            after_remain = read_remaining(page)

    except Exception as e:
        log.exception(e)
        take_screenshot(page, "99_error")
        wxpush(f"❌ Gaming4Free 续期异常: {e}")
    finally:
        time.sleep(3)
        browser.close()

    # 推送结果
    # 转换为北京时间
    now_bj = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8)))
    now_str = now_bj.strftime("%Y-%m-%d %H:%M:%S")

    if success:
        lines = [
            f"✅ Gaming4Free 续期成功",
            f"执行时间: {now_str} (北京时间)",
        ]
        if before_remain:
            lines.append(f"续期前剩余: {before_remain}")
        if after_remain:
            lines.append(f"续期后剩余: {after_remain}")
        if before_expiry:
            lines.append(f"原到期: {before_expiry}")
        if after_expiry:
            lines.append(f"新到期: {after_expiry}")
        log.info("\n".join(lines))
        # 成功不推送
    else:
        lines = [
            f"❌ Gaming4Free 续期失败（请查看截图）",
            f"执行时间: {now_str} (北京时间)",
        ]
        if before_expiry:
            lines.append(f"到期: {before_expiry}")
        msg = "\n".join(lines)
        log.info(msg)
        wxpush(msg)

if __name__ == "__main__":
    main()
