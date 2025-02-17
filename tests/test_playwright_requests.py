import json
import logging
import platform
import subprocess
from ipaddress import ip_address
from tempfile import NamedTemporaryFile

import pytest
from playwright.async_api import (
    Dialog,
    Page as PlaywrightPage,
    TimeoutError as PlaywrightTimeoutError,
)
from scrapy import Spider, Request, FormRequest
from scrapy.http.response.html import HtmlResponse

from scrapy_playwright.page import PageMethod

from tests import make_handler
from tests.mockserver import MockServer, StaticMockServer


def get_mimetype(file):
    return subprocess.run(
        ["file", "--mime-type", "--brief", file.name],
        stdout=subprocess.PIPE,
        universal_newlines=True,
        check=False,
    ).stdout.strip()


class DialogSpider(Spider):
    """A spider with a method to handle the "dialog" page event"""

    name = "dialog"

    async def handle_dialog(self, dialog: Dialog) -> None:
        self.dialog_message = dialog.message
        await dialog.dismiss()


class MixinTestCase:
    @pytest.mark.asyncio
    async def test_basic_response(self):
        async with make_handler({"PLAYWRIGHT_BROWSER_TYPE": self.browser_type}) as handler:
            with StaticMockServer() as server:
                meta = {"playwright": True, "playwright_include_page": True}
                req = Request(server.urljoin("/index.html"), meta=meta)
                resp = await handler._download_request(req, Spider("foo"))

            assert isinstance(resp, HtmlResponse)
            assert resp.request is req
            assert resp.url == req.url
            assert resp.status == 200
            assert "playwright" in resp.flags
            assert resp.css("a::text").getall() == ["Lorem Ipsum", "Infinite Scroll"]
            assert isinstance(resp.meta["playwright_page"], PlaywrightPage)
            assert resp.meta["playwright_page"].url == resp.url

            await resp.meta["playwright_page"].close()

    @pytest.mark.asyncio
    async def test_post_request(self):
        async with make_handler({"PLAYWRIGHT_BROWSER_TYPE": self.browser_type}) as handler:
            with MockServer() as server:
                req = FormRequest(
                    server.urljoin("/delay/2"), meta={"playwright": True}, formdata={"foo": "bar"}
                )
                resp = await handler._download_request(req, Spider("foo"))

            assert resp.request is req
            assert resp.url == req.url
            assert resp.status == 200
            assert "playwright" in resp.flags
            assert "Request body: foo=bar" in resp.text

    @pytest.mark.asyncio
    async def test_page_method_navigation(self):
        async with make_handler({"PLAYWRIGHT_BROWSER_TYPE": self.browser_type}) as handler:
            with StaticMockServer() as server:
                req = Request(
                    url=server.urljoin("/index.html"),
                    meta={
                        "playwright": True,
                        "playwright_page_methods": [PageMethod("click", "a.lorem_ipsum")],
                    },
                )
                resp = await handler._download_request(req, Spider("foo"))

            assert isinstance(resp, HtmlResponse)
            assert resp.request is req
            assert resp.url == server.urljoin("/lorem_ipsum.html")
            assert resp.status == 200
            assert "playwright" in resp.flags
            assert resp.css("title::text").get() == "Lorem Ipsum"
            text = resp.css("p::text").get()
            assert text == "Lorem ipsum dolor sit amet, consectetur adipiscing elit."

    @pytest.mark.asyncio
    async def test_page_method_infinite_scroll(self):
        async with make_handler({"PLAYWRIGHT_BROWSER_TYPE": self.browser_type}) as handler:
            with StaticMockServer() as server:
                req = Request(
                    url=server.urljoin("/scroll.html"),
                    headers={"User-Agent": "scrapy-playwright"},
                    meta={
                        "playwright": True,
                        "playwright_page_methods": [
                            PageMethod("wait_for_selector", selector="div.quote"),
                            PageMethod(
                                "evaluate", "window.scrollBy(0, document.body.scrollHeight)"
                            ),
                            PageMethod("wait_for_selector", selector="div.quote:nth-child(11)"),
                            PageMethod(
                                "evaluate", "window.scrollBy(0, document.body.scrollHeight)"
                            ),
                            PageMethod("wait_for_selector", selector="div.quote:nth-child(21)"),
                        ],
                    },
                )
                resp = await handler._download_request(req, Spider("foo"))

            assert isinstance(resp, HtmlResponse)
            assert resp.request is req
            assert resp.url == server.urljoin("/scroll.html")
            assert resp.status == 200
            assert "playwright" in resp.flags
            assert len(resp.css("div.quote")) == 30

    @pytest.mark.asyncio
    async def test_timeout_value(self):
        settings_dict = {
            "PLAYWRIGHT_BROWSER_TYPE": self.browser_type,
        }
        async with make_handler(settings_dict) as handler:
            assert handler.default_navigation_timeout is None

        settings_dict = {
            "PLAYWRIGHT_BROWSER_TYPE": self.browser_type,
            "PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT": None,
        }
        async with make_handler(settings_dict) as handler:
            assert handler.default_navigation_timeout is None

        settings_dict = {
            "PLAYWRIGHT_BROWSER_TYPE": self.browser_type,
            "PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT": 0,
        }
        async with make_handler(settings_dict) as handler:
            assert handler.default_navigation_timeout == 0

        settings_dict = {
            "PLAYWRIGHT_BROWSER_TYPE": self.browser_type,
            "PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT": 123,
        }
        async with make_handler(settings_dict) as handler:
            assert handler.default_navigation_timeout == 123
        settings_dict = {
            "PLAYWRIGHT_BROWSER_TYPE": self.browser_type,
            "PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT": 0.5,
        }
        async with make_handler(settings_dict) as handler:
            assert handler.default_navigation_timeout == 0.5

    @pytest.mark.asyncio
    async def test_timeout(self):
        settings_dict = {
            "PLAYWRIGHT_BROWSER_TYPE": self.browser_type,
            "PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT": 1000,
        }
        async with make_handler(settings_dict) as handler:
            with MockServer() as server:
                req = Request(server.urljoin("/delay/2"), meta={"playwright": True})
                with pytest.raises(PlaywrightTimeoutError):
                    await handler._download_request(req, Spider("foo"))

    @pytest.mark.asyncio
    async def test_context_kwargs(self):
        settings_dict = {
            "PLAYWRIGHT_BROWSER_TYPE": self.browser_type,
            "PLAYWRIGHT_CONTEXTS": {
                "default": {"java_script_enabled": False},
            },
        }
        async with make_handler(settings_dict) as handler:
            with StaticMockServer() as server:
                req = Request(
                    url=server.urljoin("/scroll.html"),
                    meta={
                        "playwright": True,
                        "playwright_page_methods": [
                            PageMethod("wait_for_selector", selector="div.quote", timeout=1000),
                        ],
                    },
                )
                with pytest.raises(PlaywrightTimeoutError):
                    await handler._download_request(req, Spider("foo"))

    @pytest.mark.asyncio
    async def test_page_method_screenshot(self):
        async with make_handler({"PLAYWRIGHT_BROWSER_TYPE": self.browser_type}) as handler:
            with NamedTemporaryFile(mode="w+b") as png_file:
                with StaticMockServer() as server:
                    req = Request(
                        url=server.urljoin("/index.html"),
                        meta={
                            "playwright": True,
                            "playwright_page_methods": {
                                "png": PageMethod("screenshot", path=png_file.name, type="png"),
                            },
                        },
                    )
                    await handler._download_request(req, Spider("foo"))

                png_file.file.seek(0)
                assert png_file.file.read() == req.meta["playwright_page_methods"]["png"].result
                assert get_mimetype(png_file) == "image/png"

    @pytest.mark.asyncio
    async def test_page_method_pdf(self):
        if self.browser_type != "chromium":
            pytest.skip("PDF generation is supported only in Chromium")

        async with make_handler({"PLAYWRIGHT_BROWSER_TYPE": self.browser_type}) as handler:
            with NamedTemporaryFile(mode="w+b") as pdf_file:
                with StaticMockServer() as server:
                    req = Request(
                        url=server.urljoin("/index.html"),
                        meta={
                            "playwright": True,
                            "playwright_page_methods": {
                                "pdf": PageMethod("pdf", path=pdf_file.name),
                            },
                        },
                    )
                    await handler._download_request(req, Spider("foo"))

                pdf_file.file.seek(0)
                assert pdf_file.file.read() == req.meta["playwright_page_methods"]["pdf"].result
                assert get_mimetype(pdf_file) == "application/pdf"

    @pytest.mark.asyncio
    async def test_user_agent(self):
        settings_dict = {
            "PLAYWRIGHT_BROWSER_TYPE": self.browser_type,
            "PLAYWRIGHT_CONTEXTS": {"default": {"user_agent": self.browser_type}},
            "USER_AGENT": None,
        }
        async with make_handler(settings_dict) as handler:
            with MockServer() as server:
                # if Scrapy's user agent is None, use the one from the Browser
                req = Request(
                    url=server.urljoin("/headers"),
                    meta={"playwright": True},
                )
                resp = await handler._download_request(req, Spider("foo"))
                headers = json.loads(resp.css("pre::text").get())
                headers = {key.lower(): value for key, value in headers.items()}
                assert headers["user-agent"] == self.browser_type

                # if Scrapy's user agent is set to some value, use it
                req = Request(
                    url=server.urljoin("/headers"),
                    meta={"playwright": True},
                    headers={"User-Agent": "foobar"},
                )
                resp = await handler._download_request(req, Spider("foo"))
                headers = json.loads(resp.css("pre::text").get())
                headers = {key.lower(): value for key, value in headers.items()}
                assert headers["user-agent"] == "foobar"

    @pytest.mark.asyncio
    async def test_use_playwright_headers(self):
        """Ignore Scrapy headers"""
        from scrapy_playwright.headers import use_playwright_headers

        settings_dict = {
            "PLAYWRIGHT_BROWSER_TYPE": self.browser_type,
            "PLAYWRIGHT_CONTEXTS": {"default": {"user_agent": self.browser_type}},
            "PLAYWRIGHT_PROCESS_REQUEST_HEADERS": use_playwright_headers,
        }
        async with make_handler(settings_dict) as handler:
            with MockServer() as server:
                req = Request(
                    url=server.urljoin("/headers"),
                    meta={"playwright": True},
                    headers={"User-Agent": "foobar", "Asdf": "qwerty"},
                )
                resp = await handler._download_request(req, Spider("foo"))
                headers = json.loads(resp.css("pre::text").get())
                headers = {key.lower(): value for key, value in headers.items()}
                assert headers["user-agent"] == self.browser_type
                assert "asdf" not in headers

    @pytest.mark.asyncio
    async def test_use_custom_headers(self):
        """Custom header processing function"""

        async def important_headers(*args, **kwargs) -> dict:
            return {"foo": "bar"}

        settings_dict = {
            "PLAYWRIGHT_BROWSER_TYPE": self.browser_type,
            "PLAYWRIGHT_CONTEXTS": {"default": {"user_agent": self.browser_type}},
            "PLAYWRIGHT_PROCESS_REQUEST_HEADERS": important_headers,
        }
        async with make_handler(settings_dict) as handler:
            with MockServer() as server:
                req = Request(
                    url=server.urljoin("/headers"),
                    meta={"playwright": True},
                    headers={"User-Agent": "foobar", "Asdf": "qwerty"},
                )
                resp = await handler._download_request(req, Spider("foo"))
                headers = json.loads(resp.css("pre::text").get())
                headers = {key.lower(): value for key, value in headers.items()}
                assert headers["foo"] == "bar"
                assert headers.get("user-agent") not in (self.browser_type, "foobar")
                assert "asdf" not in headers

    @pytest.mark.asyncio
    async def test_event_handler_dialog_callable(self):
        async with make_handler({"PLAYWRIGHT_BROWSER_TYPE": self.browser_type}) as handler:
            with StaticMockServer() as server:
                spider = DialogSpider()
                req = Request(
                    url=server.urljoin("/index.html"),
                    meta={
                        "playwright": True,
                        "playwright_page_methods": [
                            PageMethod("evaluate", "alert('foobar');"),
                        ],
                        "playwright_page_event_handlers": {
                            "dialog": spider.handle_dialog,
                        },
                    },
                )
                await handler._download_request(req, spider)

            assert spider.dialog_message == "foobar"

    @pytest.mark.asyncio
    async def test_event_handler_dialog_str(self):
        async with make_handler({"PLAYWRIGHT_BROWSER_TYPE": self.browser_type}) as handler:
            with StaticMockServer() as server:
                spider = DialogSpider()
                req = Request(
                    url=server.urljoin("/index.html"),
                    meta={
                        "playwright": True,
                        "playwright_page_methods": [
                            PageMethod("evaluate", "alert('foobar');"),
                        ],
                        "playwright_page_event_handlers": {
                            "dialog": "handle_dialog",
                        },
                    },
                )
                await handler._download_request(req, spider)

            assert spider.dialog_message == "foobar"

    @pytest.mark.asyncio
    async def test_event_handler_dialog_missing(self, caplog):
        async with make_handler({"PLAYWRIGHT_BROWSER_TYPE": self.browser_type}) as handler:
            with StaticMockServer() as server:
                spider = DialogSpider()
                req = Request(
                    url=server.urljoin("/index.html"),
                    meta={
                        "playwright": True,
                        "playwright_page_event_handlers": {
                            "dialog": "missing_method",
                        },
                    },
                )
                await handler._download_request(req, spider)

        assert (
            "scrapy-playwright",
            logging.WARNING,
            "Spider 'dialog' does not have a 'missing_method' attribute,"
            " ignoring handler for event 'dialog'",
        ) in caplog.record_tuples
        assert getattr(spider, "dialog_message", None) is None

    @pytest.mark.asyncio
    async def test_response_attributes(self):
        async with make_handler({"PLAYWRIGHT_BROWSER_TYPE": self.browser_type}) as handler:
            with MockServer() as server:
                spider = DialogSpider()
                req = Request(
                    url=server.urljoin("/index.html"),
                    meta={"playwright": True},
                )
                response = await handler._download_request(req, spider)

        assert response.ip_address == ip_address(server.address)

    @pytest.mark.asyncio
    async def test_abort_requests(self):
        settings_dict = {
            "PLAYWRIGHT_BROWSER_TYPE": self.browser_type,
            "PLAYWRIGHT_ABORT_REQUEST": lambda req: req.resource_type == "image",
        }
        async with make_handler(settings_dict) as handler:
            with StaticMockServer() as server:
                req = Request(
                    url=server.urljoin("/gallery.html"),
                    meta={"playwright": True},
                )
                await handler._download_request(req, Spider("foo"))

                req_prefix = "playwright/request_count"
                resp_prefix = "playwright/response_count"
                assert handler.stats.get_value(f"{req_prefix}/resource_type/document") == 1
                assert handler.stats.get_value(f"{req_prefix}/resource_type/image") == 3
                assert handler.stats.get_value(f"{resp_prefix}/resource_type/document") == 1
                assert handler.stats.get_value(f"{resp_prefix}/resource_type/image") is None
                assert handler.stats.get_value(f"{req_prefix}/aborted") == 3


class TestCaseChromium(MixinTestCase):
    browser_type = "chromium"


class TestCaseFirefox(MixinTestCase):
    browser_type = "firefox"


@pytest.mark.skipif(platform.system() != "Darwin", reason="Test WebKit only on Darwin")
class TestCaseWebkit(MixinTestCase):
    browser_type = "webkit"
