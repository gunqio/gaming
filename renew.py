#!/usr/bin/python3.12

import sys
import re
import time
import random
import string
from concurrent.futures import ThreadPoolExecutor, as_completed

from cloakbrowser import launch

SERVERS = [
    {"url": "https://g4f.gg/deku", "name": "Deku"},
    {"url": "https://g4f.gg/rena", "name": "Rena"},
]


def random_voter_name():
    return (
        random.choice(string.ascii_uppercase)
        + ''.join(
            random.choices(
                string.ascii_lowercase,
                k=4
            )
        )
    )


def _parse_time(s):
    if s == "N/A" or not s:
        return None

    try:
        parts = s.strip().split(":")

        if len(parts) == 3:
            return (
                int(parts[0]) * 3600
                + int(parts[1]) * 60
                + int(parts[2])
            )

        if len(parts) == 2:
            return (
                int(parts[0]) * 60
                + int(parts[1])
            )

    except:
        pass

    return None


def _extract_remaining_time(text):

    m = re.search(
        r"SERVER TIME REMAINING\s*([\d:]+)",
        text,
        re.I
    )

    return m.group(1) if m else "N/A"


def _safe_goto(page, url, name):

    for attempt in range(3):

        try:

            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=30000
            )

            #
            # 给页面3秒渲染投票表单
            #
            page.wait_for_timeout(3000)

            #
            # 强行停止所有剩余加载（广告等）
            #
            page.evaluate("window.stop()")

            print(
                f"[{name}] 页面已加载，已停止广告资源",
                file=sys.stderr
            )

            return

        except Exception as e:

            msg = str(e)

            if any(x in msg for x in (
                "ERR_NETWORK_CHANGED",
                "ERR_CONNECTION_RESET",
                "ERR_TIMED_OUT",
            )):

                print(
                    f"[{name}] 网络异常({attempt+1}/3): {msg}",
                    file=sys.stderr
                )

                page.wait_for_timeout(5000)

                continue

            raise

    raise RuntimeError(
        f"{name} 页面加载失败"
    )


def _click_turnstile(page, name):

    try:

        iframe = page.locator(
            'iframe[src*="turnstile"]'
        ).first

        if iframe.count() == 0:
            return False

        box = iframe.bounding_box()

        if not box:
            return False

        page.mouse.click(
            box["x"] + box["width"] / 2,
            box["y"] + box["height"] / 2
        )

        print(
            f"[{name}] 已尝试点击 Turnstile",
            file=sys.stderr
        )

        return True

    except Exception as e:

        print(
            f"[{name}] Turnstile点击失败: {e}",
            file=sys.stderr
        )

        return False


def _run_single_attempt(url, name):

    browser = launch(
        headless=False,
        humanize=True,
        geoip=True,
        proxy="socks5://127.0.0.1:7928"
    )

    try:

        page = browser.new_page()

        voter_name = random_voter_name()

        print(
            f"[{name}] Voter: {voter_name}",
            file=sys.stderr
        )

        vote_result = {
            "request_sent": False,
            "status": None,
        }

        def on_request(req):

            try:

                if (
                    "/vote" in req.url
                    and req.method == "POST"
                ):
                    vote_result["request_sent"] = True

                    print(
                        f"[{name}] Vote POST Sent",
                        file=sys.stderr
                    )

            except:
                pass

        def on_response(resp):

            try:

                if "/vote" in resp.url:

                    vote_result["status"] = resp.status

                    print(
                        f"[{name}] Vote Response: {resp.status}",
                        file=sys.stderr
                    )

            except:
                pass

        page.on("request", on_request)
        page.on("response", on_response)

        page.on(
            "pageerror",
            lambda err: print(
                f"[{name}] page_error: {err}",
                file=sys.stderr
            )
        )

        _safe_goto(
            page,
            url,
            name
        )

        page.wait_for_timeout(3000)

        body = page.evaluate(
            "document.body?.innerText || ''"
        )

        if "Come back in" in body:

            return (
                False,
                f"⏳ {name} 冷却中",
                None
            )

        before_str = _extract_remaining_time(
            body
        )

        before_secs = _parse_time(
            before_str
        )

        print(
            f"[{name}] Before: {before_str}",
            file=sys.stderr
        )

        page.wait_for_selector(
            'input[name="voter_name"]',
            timeout=10000
        )

        page.fill(
            'input[name="voter_name"]',
            voter_name
        )

        page.locator(
            "button.vote-btn"
        ).click(force=True)

        print(
            f"[{name}] 点击 Vote",
            file=sys.stderr
        )

        success_detected = False
        after_str = "N/A"

        success_patterns = [
            "minutes added",
            "minute added",
            "thanks for supporting the server",
        ]

        #
        # 主等待阶段（30秒）
        #
        for _ in range(15):

            page.wait_for_timeout(2000)

            try:

                current_text = page.evaluate(
                    "document.body?.innerText || ''"
                )

            except:
                continue

            lower_text = current_text.lower()

            after_str = _extract_remaining_time(
                current_text
            )

            for p in success_patterns:

                if p in lower_text:

                    print(
                        f"[{name}] Success Pattern: {p}",
                        file=sys.stderr
                    )

                    success_detected = True
                    break

            if success_detected:
                break

        #
        # Turnstile补救
        #
        if (
            not success_detected
            and vote_result["status"] is None
        ):

            print(
                f"[{name}] Turnstile超时，尝试触发",
                file=sys.stderr
            )

            _click_turnstile(
                page,
                name
            )

            for _ in range(8):

                page.wait_for_timeout(2000)

                try:

                    current_text = page.evaluate(
                        "document.body?.innerText || ''"
                    )

                except:
                    continue

                lower_text = current_text.lower()

                after_str = _extract_remaining_time(
                    current_text
                )

                for p in success_patterns:

                    if p in lower_text:

                        print(
                            f"[{name}] Success Pattern: {p}",
                            file=sys.stderr
                        )

                        success_detected = True
                        break

                if (
                    success_detected
                    or vote_result["status"] == 302
                ):
                    break

        try:

            final_text = page.evaluate(
                "document.body?.innerText || ''"
            )

            after_str = _extract_remaining_time(
                final_text
            )

        except:
            pass

        after_secs = _parse_time(
            after_str
        )

        print(
            f"[{name}] After: {after_str}",
            file=sys.stderr
        )

        #
        # 最可靠成功判断
        #
        if (
            vote_result["status"] == 302
            and before_secs
            and after_secs
        ):

            diff = (
                after_secs - before_secs
            ) // 60

            return (
                True,
                f"✅ {name} 续期成功 (+{diff}分钟) 剩余:{after_str}",
                after_str
            )

        if success_detected:

            return (
                True,
                f"✅ {name} 检测到成功提示",
                after_str
            )

        if (
            before_secs
            and after_secs
            and after_secs > before_secs + 3000
        ):

            diff = (
                after_secs - before_secs
            ) // 60

            return (
                True,
                f"✅ {name} 剩余时间增加 {diff} 分钟",
                after_str
            )

        return (
            False,
            f"❌ {name} 未检测到续期成功",
            after_str
        )

    finally:

        try:
            browser.close()
        except:
            pass


def renew_server(url, name):

    for attempt in range(2):

        try:

            success, msg, tl = _run_single_attempt(
                url,
                name
            )

            if success:
                return success, msg, tl

            if "冷却中" in msg:
                return success, msg, tl

            print(
                f"[{name}] 重试 #{attempt+1}",
                file=sys.stderr
            )

        except Exception as e:

            print(
                f"[{name}] 异常: {e}",
                file=sys.stderr
            )

            if attempt == 1:

                return (
                    False,
                    f"❌ {name} 错误: {e}",
                    None
                )

        if attempt < 1:
            time.sleep(3)

    return (
        False,
        f"❌ {name} 续期失败",
        None
    )


def main():

    # ===== 浏览器验证代理 IP =====
    try:
        print("\n===== 验证代理 IP =====", file=sys.stderr)
        check_browser = launch(
            headless=False,
            humanize=True,
            proxy="socks5://127.0.0.1:7928"
        )
        check_page = check_browser.new_page()
        check_page.goto(
            "https://ip.sb",
            wait_until="domcontentloaded",
            timeout=30000
        )
        
        check_page.wait_for_timeout(3000)
        time.sleep(5)
        check_page.evaluate("window.stop()")
        body_text = check_page.evaluate(
            "document.body?.innerText || ''"
        )
        print(body_text, file=sys.stderr)

        ip_match = re.search(
            r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})",
            body_text
        )

        if ip_match:
            print(
                f"✅ 浏览器代理已连接，出站 IP: {ip_match.group(1)}",
                file=sys.stderr
            )
        else:
            print(
                "⚠️ 未能识别出站 IP",
                file=sys.stderr
            )

        check_browser.close()
        print("===== 验证完成 =====\n", file=sys.stderr)

    except Exception as e:
        print(
            f"❌ 代理 IP 检查失败: {e}",
            file=sys.stderr
        )

    # ===== 并行投票 =====
    results = [None] * len(SERVERS)

    def run_task(index, server):
        print(
            f"\n=== {server['name']} ===",
            file=sys.stderr
        )
        ok, msg, tl = renew_server(
            server["url"],
            server["name"]
        )
        return index, ok, msg, tl

    with ThreadPoolExecutor(max_workers=len(SERVERS)) as executor:
        futures = [
            executor.submit(run_task, i, s)
            for i, s in enumerate(SERVERS)
        ]

        for future in as_completed(futures):
            index, ok, msg, tl = future.result()
            results[index] = (ok, msg, tl)

    print("\n" + "=" * 30)
    print("📊 G4F 服务器续期报告")

    for _, msg, _ in results:
        print(f"  {msg}")


if __name__ == "__main__":
    main()
