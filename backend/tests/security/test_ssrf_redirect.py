"""Verify safe_urlopen blocks malicious redirects.

Without per-hop validation, an external server can answer the first
GET (which we already validated) with `302 Location: 169.254.169.254`
and urllib follows it into cloud metadata. We test that the redirect
handler catches this without making real network calls — by exercising
the handler directly.
"""

import http.client
import io
import urllib.error
import urllib.request

import pytest

from app.security.network import (
    MAX_REDIRECTS,
    SSRFBlocked,
    _SSRFSafeRedirectHandler,
    assert_public_url,
    safe_urlopen,
)


class TestRedirectHandler:
    """Direct-mode tests of the redirect handler — no real I/O."""

    def _call(self, target_url: str):
        handler = _SSRFSafeRedirectHandler()
        req = urllib.request.Request("https://example.com/start")
        fake_fp = io.BytesIO(b"")
        return handler.redirect_request(
            req, fake_fp, 302, "Found",
            http.client.HTTPMessage(),
            target_url,
        )

    def test_blocks_redirect_to_loopback(self):
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            self._call("http://127.0.0.1/admin")
        assert "redirect blocked" in str(exc_info.value)

    def test_blocks_redirect_to_aws_metadata(self):
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            self._call("http://169.254.169.254/latest/meta-data/")
        assert "redirect blocked" in str(exc_info.value)

    def test_blocks_redirect_to_private_lan(self):
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            self._call("http://10.0.0.5/")
        assert "redirect blocked" in str(exc_info.value)

    def test_blocks_redirect_to_file_scheme(self):
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            self._call("file:///etc/passwd")
        assert "redirect blocked" in str(exc_info.value)


class TestSafeUrlopenInitialUrl:
    """Initial URL is validated before any I/O."""

    def test_blocks_loopback(self):
        with pytest.raises(SSRFBlocked):
            safe_urlopen("http://127.0.0.1/x")

    def test_blocks_cloud_metadata(self):
        with pytest.raises(SSRFBlocked):
            safe_urlopen("http://169.254.169.254/")

    def test_blocks_private_lan(self):
        with pytest.raises(SSRFBlocked):
            safe_urlopen("http://10.10.10.10/")

    def test_blocks_link_local_v6(self):
        with pytest.raises(SSRFBlocked):
            safe_urlopen("http://[fe80::1]/")

    def test_blocks_unsupported_scheme(self):
        with pytest.raises(SSRFBlocked):
            safe_urlopen("ftp://example.com/")

    def test_blocks_no_host(self):
        with pytest.raises(SSRFBlocked):
            safe_urlopen("http:///path")


class TestRedirectCap:
    """Sanity check on the redirect ceiling."""

    def test_max_redirects_is_tight(self):
        # Default urllib is 10. We trim to 3 to refuse open redirect chains.
        assert MAX_REDIRECTS == 3
        assert MAX_REDIRECTS < 10
