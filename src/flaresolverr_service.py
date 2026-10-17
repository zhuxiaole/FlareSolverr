import asyncio
import logging
import platform
import sys
import threading
import time
from datetime import timedelta
from html import escape
from urllib.parse import unquote, quote
import urllib.parse

import utils
from dtos import (STATUS_ERROR, STATUS_OK, ChallengeResolutionResultT,
                  ChallengeResolutionT, HealthResponse, IndexResponse,
                  V1RequestBase, V1ResponseBase)
from sessions import SessionsStorage

ACCESS_DENIED_TITLES = [
    # Cloudflare
    'Access denied',
    # Cloudflare http://bitturk.net/ Firefox
    'Attention Required! | Cloudflare'
]
ACCESS_DENIED_SELECTORS = [
    # Cloudflare
    'div.cf-error-title span.cf-code-label span',
    # Cloudflare http://bitturk.net/ Firefox
    '#cf-error-details div.cf-error-overview h1'
]
CHALLENGE_TITLES = [
    # Cloudflare
    'Just a moment...',
    'Checking your browser...',
    'Please Wait... | Cloudflare',
    # DDoS-GUARD
    'DDoS-Guard'
]
CHALLENGE_SELECTORS = [
    # Cloudflare
    '#cf-challenge-running', '.ray_id', '.attack-box', '#cf-please-wait', '#challenge-spinner', '#trk_jschal_js', '#turnstile-wrapper', '.lds-ring',
    # Custom CloudFlare for EbookParadijs, Film-Paleis, MuziekFabriek and Puur-Hollands
    'td.info #js_info',
    # Fairlane / pararius.com
    'div.vc div.text-box h2'
]

TURNSTILE_SELECTORS = [
    "input[name='cf-turnstile-response']",
    "#turnstile-wrapper",
    ".attendance-card--verify"
]

SESSIONS_STORAGE = SessionsStorage()


def is_cloudflare_challenge(text: str) -> bool:
    signals = [
        "cf-turnstile-wrapper",
        "cf-turnstile-response",
        "challenges.cloudflare.com",
        "<title>Just a moment...</title>",
        "<title>Checking your browser...</title>",
        "attendance-card--verify",
        "dev8133_cloudflare"
    ]
    return any(sig in text for sig in signals)


def test_browser_installation():
    logging.info("Testing web browser installation...")
    logging.info("Platform: " + platform.platform())

    chrome_exe_path = utils.find_browser_executable()
    if chrome_exe_path is None:
        logging.error("Chrome / Chromium web browser not installed!")
        sys.exit(1)
    else:
        logging.info("Chrome / Chromium path: " + chrome_exe_path)

    chrome_major_version = utils.get_chrome_major_version()
    if chrome_major_version == '':
        logging.warning("Chrome / Chromium version not detected, continuing...")
    else:
        logging.info("Chrome / Chromium major version: " + chrome_major_version)

    logging.info("Launching web browser...")
    user_agent = utils.get_user_agent()
    logging.info("FlareSolverr User-Agent: " + user_agent)
    logging.info("Test successful!")


def index_endpoint() -> IndexResponse:
    res = IndexResponse({})
    res.msg = "FlareSolverr is ready!"
    res.version = utils.get_flaresolverr_version()
    res.userAgent = utils.get_user_agent()
    return res


def health_endpoint() -> HealthResponse:
    res = HealthResponse({})
    res.status = STATUS_OK
    return res


def controller_v1_endpoint(req: V1RequestBase) -> V1ResponseBase:
    start_ts = int(time.time() * 1000)
    logging.info(f"Incoming request => POST /v1 body: {utils.object_to_dict(req)}")
    res: V1ResponseBase
    try:
        res = _controller_v1_handler(req)
    except Exception as e:
        res = V1ResponseBase({})
        res.__error_500__ = True
        res.status = STATUS_ERROR
        res.message = "Error: " + str(e)
        logging.error(res.message)

    res.startTimestamp = start_ts
    res.endTimestamp = int(time.time() * 1000)
    res.version = utils.get_flaresolverr_version()
    logging.debug(f"Response => POST /v1 body: {utils.object_to_dict(res)}")
    logging.info(f"Response in {(res.endTimestamp - res.startTimestamp) / 1000} s")
    return res


def _controller_v1_handler(req: V1RequestBase) -> V1ResponseBase:
    if req.cmd is None:
        raise Exception("Request parameter 'cmd' is mandatory.")
    if req.headers is not None:
        logging.warning("Request parameter 'headers' was removed in FlareSolverr v2.")
    if req.userAgent is not None:
        logging.warning("Request parameter 'userAgent' was removed in FlareSolverr v2.")

    if req.maxTimeout is None or int(req.maxTimeout) < 1:
        req.maxTimeout = 60000

    res: V1ResponseBase
    if req.cmd == 'sessions.create':
        res = _cmd_sessions_create(req)
    elif req.cmd == 'sessions.list':
        res = _cmd_sessions_list(req)
    elif req.cmd == 'sessions.destroy':
        res = _cmd_sessions_destroy(req)
    elif req.cmd == 'request.get':
        res = _cmd_request_get(req)
    elif req.cmd == 'request.post':
        res = _cmd_request_post(req)
    else:
        raise Exception(f"Request parameter 'cmd' = '{req.cmd}' is invalid.")

    return res


def _cmd_request_get(req: V1RequestBase) -> V1ResponseBase:
    if req.url is None:
        raise Exception("Request parameter 'url' is mandatory in 'request.get' command.")
    if req.postData is not None:
        raise Exception("Cannot use 'postBody' when sending a GET request.")
    if req.returnRawHtml is not None:
        logging.warning("Request parameter 'returnRawHtml' was removed in FlareSolverr v2.")
    if req.download is not None:
        logging.warning("Request parameter 'download' was removed in FlareSolverr v2.")

    challenge_res = _resolve_challenge(req, 'GET')
    res = V1ResponseBase({})
    res.status = challenge_res.status
    res.message = challenge_res.message
    res.solution = challenge_res.result
    return res


def _cmd_request_post(req: V1RequestBase) -> V1ResponseBase:
    if req.postData is None:
        raise Exception("Request parameter 'postData' is mandatory in 'request.post' command.")
    if req.returnRawHtml is not None:
        logging.warning("Request parameter 'returnRawHtml' was removed in FlareSolverr v2.")
    if req.download is not None:
        logging.warning("Request parameter 'download' was removed in FlareSolverr v2.")

    challenge_res = _resolve_challenge(req, 'POST')
    res = V1ResponseBase({})
    res.status = challenge_res.status
    res.message = challenge_res.message
    res.solution = challenge_res.result
    return res


def _cmd_sessions_create(req: V1RequestBase) -> V1ResponseBase:
    logging.debug("Creating new session...")
    session, fresh = SESSIONS_STORAGE.create(session_id=req.session, proxy=req.proxy)
    session_id = session.session_id

    if not fresh:
        return V1ResponseBase({
            "status": STATUS_OK,
            "message": "Session already exists.",
            "session": session_id
        })

    return V1ResponseBase({
        "status": STATUS_OK,
        "message": "Session created successfully.",
        "session": session_id
    })


def _cmd_sessions_list(req: V1RequestBase) -> V1ResponseBase:
    session_ids = SESSIONS_STORAGE.session_ids()
    return V1ResponseBase({
        "status": STATUS_OK,
        "message": "",
        "sessions": session_ids
    })


def _cmd_sessions_destroy(req: V1RequestBase) -> V1ResponseBase:
    session_id = req.session
    existed = SESSIONS_STORAGE.destroy(session_id)
    if not existed:
        raise Exception("The session doesn't exist.")

    return V1ResponseBase({
        "status": STATUS_OK,
        "message": "The session has been removed."
    })


def _resolve_challenge(req: V1RequestBase, method: str) -> ChallengeResolutionT:
    timeout = int(req.maxTimeout) / 1000
    start_ts = time.time()
    try:
        if req.session:
            session_id = req.session
            ttl = timedelta(minutes=req.session_ttl_minutes) if req.session_ttl_minutes else None
            session, fresh = SESSIONS_STORAGE.get(session_id, ttl)

            if fresh:
                logging.debug(f"new session created to perform the request (session_id={session_id})")
            else:
                logging.debug(f"existing session is used to perform the request (session_id={session_id}, "
                              f"lifetime={str(session.lifetime())}, ttl={str(ttl)})")

            return utils.run_async(_async_resolve_challenge(req, session.browser, method), timeout=timeout)
        else:
            # 信号量控制并发数（默认最多 3 个），超出时排队等待可用槽位
            if not utils.browser_semaphore.acquire(timeout=timeout):
                raise Exception(f'Error solving the challenge. Timeout after {timeout} seconds waiting for browser slot.')
            try:
                remaining_timeout = max(1.0, timeout - (time.time() - start_ts))

                async def _solve_ephemeral():
                    browser = await utils.start_browser(req.proxy)
                    try:
                        return await _async_resolve_challenge(req, browser, method)
                    finally:
                        await utils.stop_browser(browser)

                return utils.run_async(_solve_ephemeral(), timeout=remaining_timeout)
            finally:
                utils.browser_semaphore.release()
    except TimeoutError:
        raise Exception(f'Error solving the challenge. Timeout after {timeout} seconds.')
    except Exception as e:
        raise Exception('Error solving the challenge. ' + str(e).replace('\n', '\\n'))


async def _async_resolve_challenge(req: V1RequestBase, browser, method: str) -> ChallengeResolutionT:
    import zendriver.cdp.network as network

    page = browser.main_tab or await browser.get("about:blank")

    # 1. 媒体资源拦截 (图片/CSS/字体)
    disable_media = utils.get_config_disable_media()
    if req.disableMedia is not None:
        disable_media = req.disableMedia
    if disable_media:
        block_urls = [
            "*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp", "*.bmp", "*.svg", "*.ico",
            "*.PNG", "*.JPG", "*.JPEG", "*.GIF", "*.WEBP", "*.BMP", "*.SVG", "*.ICO",
            "*.tiff", "*.tif", "*.jpe", "*.apng", "*.avif", "*.heic", "*.heif",
            "*.TIFF", "*.TIF", "*.JPE", "*.APNG", "*.AVIF", "*.HEIC", "*.HEIF",
            "*.css", "*.CSS",
            "*.woff", "*.woff2", "*.ttf", "*.otf", "*.eot",
            "*.WOFF", "*.WOFF2", "*.TTF", "*.OTF", "*.EOT"
        ]
        try:
            await page.send(network.enable())
            await page.send(network.set_blocked_ur_ls(urls=block_urls))
        except Exception as e:
            logging.debug("Network.setBlockedURLs failed or unsupported: %s", e)

    # 2. 页面导航
    logging.debug(f"Navigating to... {req.url}")
    post_html = None
    if method == "POST":
        post_html = _build_post_html(req)
        await page.get(f"data:text/html;charset=utf-8,{post_html}")
    else:
        await page.get(req.url)

    # 3. 注入 Cookies
    if req.cookies and len(req.cookies) > 0:
        logging.debug("Setting cookies...")
        parsed = urllib.parse.urlparse(req.url)
        default_domain = parsed.hostname or ""
        cookie_params = []
        for c in req.cookies:
            domain = c.get("domain") or default_domain
            path = c.get("path") or "/"
            same_site_val = None
            if c.get("sameSite"):
                try:
                    same_site_val = network.CookieSameSite(c["sameSite"].lower())
                except Exception:
                    pass
            param = network.CookieParam(
                name=c["name"],
                value=c["value"],
                domain=domain,
                path=path,
                secure=c.get("secure"),
                http_only=c.get("httpOnly"),
                same_site=same_site_val,
                expires=c.get("expires") or c.get("expiry")
            )
            cookie_params.append(param)
        await browser.cookies.set_all(cookie_params)
        # 注入 Cookie 后重新加载
        if method == "POST":
            await page.get(f"data:text/html;charset=utf-8,{post_html}")
        else:
            await page.get(req.url)

    # 4. 获取初始内容及标题
    await asyncio.sleep(1)
    content = await page.get_content()
    page_title = await page.evaluate("document.title") or ""

    if utils.get_config_log_html():
        logging.debug(f"Response HTML:\n{content}")

    # 检查访问拒绝
    for title in ACCESS_DENIED_TITLES:
        if page_title.startswith(title):
            raise Exception('Cloudflare has blocked this request. '
                            'Probably your IP is banned for this site, check in your web browser.')
    for selector in ACCESS_DENIED_SELECTORS:
        if selector in content:
            raise Exception('Cloudflare has blocked this request. '
                            'Probably your IP is banned for this site, check in your web browser.')

    # 检查是否存在质询
    challenge_found = False
    for title in CHALLENGE_TITLES:
        if title.lower() == page_title.lower():
            challenge_found = True
            logging.info("Challenge detected. Title found: " + page_title)
            break
    if not challenge_found:
        for selector in CHALLENGE_SELECTORS:
            if selector in content:
                challenge_found = True
                logging.info("Challenge detected. Selector found: " + selector)
                break
    if not challenge_found and is_cloudflare_challenge(content):
        challenge_found = True
        logging.info("Challenge detected by Cloudflare signatures.")

    res = ChallengeResolutionT({})
    res.status = STATUS_OK
    res.message = ""

    max_timeout = int(req.maxTimeout) / 1000
    start_ts = time.time()
    attempt = 0

    if challenge_found:
        while True:
            attempt += 1
            if time.time() - start_ts > max_timeout:
                raise Exception(f'Error solving the challenge. Timeout after {max_timeout} seconds.')

            # 自动调用 zendriver 原生 verify_cf 穿透 Turnstile / 5秒盾
            try:
                await page.verify_cf(click_delay=2, timeout=6)
            except Exception:
                # 兼容非标准/内嵌 Shadow DOM Turnstile 部件，执行坐标点击降级处理
                try:
                    rect = await page.evaluate("""(() => {
                        const el = document.getElementById('turnstile') || 
                                   document.querySelector('.attendance-card--verify') ||
                                   document.querySelector('#cf-turnstile-wrapper');
                        if (el) {
                            const r = el.getBoundingClientRect();
                            return {x: r.x, y: r.y, w: r.width, h: r.height};
                        }
                        return null;
                    })()""")
                    if rect and rect.get("x") is not None:
                        await page.mouse_click(rect["x"] + min(25, rect.get("w", 50)/2),
                                               rect["y"] + min(32, rect.get("h", 60)/2))
                except Exception:
                    pass

            await asyncio.sleep(2)
            content = await page.get_content()
            page_title = await page.evaluate("document.title") or ""

            has_challenge_title = any(t.lower() == page_title.lower() for t in CHALLENGE_TITLES)
            has_challenge_selector = any(s in content for s in CHALLENGE_SELECTORS)
            has_cf_signals = is_cloudflare_challenge(content)

            cookies_now = await browser.cookies.get_all()
            has_cf_clearance = any(c.name == "cf_clearance" for c in cookies_now)

            if not has_challenge_title and not has_challenge_selector and not has_cf_signals:
                logging.info(f"Challenge solved! Final page title: '{page_title}'")
                res.message = "Challenge solved!"
                break
            elif has_cf_clearance:
                logging.debug("cf_clearance obtained, waiting for page to navigate to target...")
                redirected = False
                for _ in range(8):
                    await asyncio.sleep(1)
                    content = await page.get_content()
                    page_title = await page.evaluate("document.title") or ""
                    has_challenge_title = any(t.lower() == page_title.lower() for t in CHALLENGE_TITLES)
                    has_challenge_selector = any(s in content for s in CHALLENGE_SELECTORS)
                    has_cf_signals = is_cloudflare_challenge(content)
                    if not has_challenge_title and not has_challenge_selector and not has_cf_signals:
                        redirected = True
                        break

                if not redirected:
                    # 页面未在预期时间内自动跳转，携带新鲜的 cf_clearance 主动重新加载目标 URL
                    logging.info("Page did not redirect automatically, reloading target URL with clearance...")
                    if method == "POST":
                        await page.get(f"data:text/html;charset=utf-8,{post_html}")
                    else:
                        await page.get(req.url)
                    await asyncio.sleep(2)
                    content = await page.get_content()
                    page_title = await page.evaluate("document.title") or ""

                logging.info(f"Challenge solved! Final page title: '{page_title}'")
                res.message = "Challenge solved!"
                break
            elif "attendance-card--done" in content:
                logging.info(f"Challenge solved (card done)! Final page title: '{page_title}'")
                res.message = "Challenge solved!"
                break

            logging.debug(f"Waiting for challenge resolution (attempt {attempt}, title={page_title})")
    else:
        logging.info("Challenge not detected!")
        res.message = "Challenge not detected!"

    # 5. 提取 Turnstile Token
    turnstile_token = await page.evaluate(
        "document.querySelector(\"input[name='cf-turnstile-response']\")?.value || "
        "document.getElementById('cf-token')?.value || null"
    )

    # 6. 构建并返回结构化响应
    challenge_res = ChallengeResolutionResultT({})
    current_url = await page.evaluate("window.location.href") or req.url
    challenge_res.url = current_url
    challenge_res.status = 200
    challenge_res.userAgent = await page.evaluate("navigator.userAgent")
    challenge_res.turnstile_token = turnstile_token
    challenge_res.headers = {}

    if not req.returnOnlyCookies:
        if req.waitInSeconds and req.waitInSeconds > 0:
            logging.info(f"Waiting {req.waitInSeconds} seconds before returning the response...")
            await asyncio.sleep(req.waitInSeconds)
        challenge_res.response = await page.get_content()

    if req.returnScreenshot:
        challenge_res.screenshot = await page.screenshot_b64()

    all_cookies = await browser.cookies.get_all()
    cookies_list = []
    for c in all_cookies:
        c_dict = c.to_json()
        # session cookie 处理：若 expires <= 0 则删除，避免 .NET (Jackett) 将其解析为 1969 年并判定为已过期丢弃
        if c_dict.get("expires") is not None:
            if c_dict["expires"] > 0:
                c_dict["expiry"] = int(c_dict["expires"])
            else:
                del c_dict["expires"]
        if "expiry" in c_dict and c_dict["expiry"] <= 0:
            del c_dict["expiry"]

        if c_dict.get("sameSite"):
            c_dict["sameSite"] = c_dict["sameSite"].capitalize()

        cookies_list.append(c_dict)
    challenge_res.cookies = cookies_list

    res.result = challenge_res
    return res


def _build_post_html(req: V1RequestBase) -> str:
    post_form = f'<form id="hackForm" action="{req.url}" method="POST">'
    query_string = req.postData if req.postData and req.postData[0] != '?' else req.postData[1:] if req.postData else ''
    pairs = query_string.split('&')
    for pair in pairs:
        parts = pair.split('=', 1)
        try:
            name = unquote(parts[0])
        except Exception:
            name = parts[0]
        if name == 'submit':
            continue
        try:
            value = unquote(parts[1]) if len(parts) > 1 else ''
        except Exception:
            value = parts[1] if len(parts) > 1 else ''
        value = value.replace('"', '&quot;')
        post_form += f'<input type="text" name="{escape(quote(name))}" value="{escape(quote(value))}"><br>'
    post_form += '</form>'
    return f"""
        <!DOCTYPE html>
        <html>
        <body>
            {post_form}
            <script>document.getElementById('hackForm').submit();</script>
        </body>
        </html>"""
