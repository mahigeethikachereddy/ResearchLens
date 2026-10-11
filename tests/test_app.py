import csv
import io
from unittest.mock import patch

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.main import app, extract, fetch, render_report, ResearchResult, validate_url

client = TestClient(app)
BODY = {"url": "https://example.com", "demo": True}


def test_demo_and_downloads(monkeypatch):
    monkeypatch.delenv("RESEARCH_API_KEY", raising=False)
    result = client.post("/research", json=BODY)
    assert result.status_code == 200
    assert result.json()["mode"] == "demo"
    assert "DEMO" in result.json()["summary"]
    exported = client.post("/research/export/csv", json=BODY)
    rows = list(csv.reader(io.StringIO(exported.text.lstrip('\ufeff'))))
    assert len(rows) == 2 and rows[1][0] == result.json()["title"]
    assert client.post("/research/export/html", json=BODY).status_code == 200
    assert client.post("/research/export/pdf", json=BODY).status_code == 400


def test_auth(monkeypatch):
    monkeypatch.setenv("RESEARCH_API_KEY", "secret")
    assert client.post("/research", json=BODY).status_code == 401
    assert client.post("/research", json=BODY, headers={"X-API-Key": "secret"}).status_code == 200


def test_extraction():
    title, text, truncated = extract('<title>Test</title><nav>junk</nav><main><p>' + 'evidence ' * 4000 + '</p><script>bad</script></main>')
    assert title == "Test" and "junk" not in text and "bad" not in text
    assert truncated and len(text) == 24000


def test_network_validation(monkeypatch):
    monkeypatch.setenv("ALLOWED_DOMAINS", "example.com")
    for url in ["http://example.com", "https://user:password@example.com", "https://example.com:8080"]:
        with pytest.raises(HTTPException):
            validate_url(url)
    with patch("app.main.socket.getaddrinfo", return_value=[(2, 1, 6, '', ('127.0.0.1', 443))]):
        with pytest.raises(HTTPException):
            validate_url("https://example.com")


def test_robots_and_redirects(monkeypatch):
    monkeypatch.setenv("ALLOWED_DOMAINS", "example.com")
    original = httpx.Client
    def run(handler):
        with patch("app.main.validate_url", return_value=__import__('urllib.parse', fromlist=['urlsplit']).urlsplit('https://example.com')), patch("app.main.httpx.Client", side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw)):
            return fetch("https://example.com/page")
    with pytest.raises(HTTPException) as blocked:
        run(lambda req: httpx.Response(200, text="User-agent: *\nDisallow: /"))
    assert blocked.value.status_code == 403
    with pytest.raises(HTTPException) as redirect:
        run(lambda req: httpx.Response(404) if req.url.path == '/robots.txt' else httpx.Response(302, headers={'location': 'https://other.com'}))
    assert redirect.value.status_code == 422


def test_report_escapes_html():
    result = ResearchResult(title='<script>alert(1)</script>', url='https://example.com', retrieved_at='today', summary='<img src=x>', extracted_text='evidence', truncated=False, mode='demo')
    report = render_report(result)
    assert '<script>' not in report and '&lt;img' in report


def test_mocked_ai(monkeypatch):
    import json
    monkeypatch.setenv('GEMINI_API_KEY', 'test-not-real')
    monkeypatch.setenv('GEMINI_MODEL', 'test-model')
    monkeypatch.delenv('RESEARCH_API_KEY', raising=False)
    original = httpx.Client
    def handler(request):
        assert request.url.path.endswith('/models/test-model:generateContent')
        assert request.headers['x-goog-api-key'] == 'test-not-real'
        assert 'key=' not in str(request.url)
        payload = json.loads(request.content)
        assert 'untrusted data' in payload['systemInstruction']['parts'][0]['text']
        assert 'Evidence' in payload['contents'][0]['parts'][0]['text']
        return httpx.Response(200, json={'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': 'private reasoning', 'thought': True}, {'text': 'Overview\nA grounded summary'}]}}]})
    with patch('app.main.fetch', return_value=('Title', 'Evidence '*20, False)), patch('app.main.httpx.Client', side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw)):
        response = client.post('/research', json={'url': 'https://example.com'})
    assert response.status_code == 200
    assert response.json()['mode'] == 'gemini'
    assert response.json()['model'] == 'test-model'
    assert response.json()['summary'] == 'Overview\nA grounded summary'


def test_missing_key(monkeypatch):
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    monkeypatch.delenv('RESEARCH_API_KEY', raising=False)
    with patch('app.main.fetch', return_value=('Title', 'Evidence '*20, False)):
        assert client.post('/research', json={'url':'https://example.com'}).status_code == 503


@pytest.mark.parametrize('status,payload', [
    (403, {'error': {'message': 'sensitive upstream details'}}),
    (429, {}),
    (200, {'candidates': []}),
    (200, {'candidates': [{'finishReason': 'MAX_TOKENS', 'content': {'parts': [{'text': 'partial'}]}}]}),
    (200, {'promptFeedback': {'blockReason': 'SAFETY'}}),
])
def test_gemini_failures(monkeypatch, status, payload):
    from app.main import summarize
    monkeypatch.setenv('GEMINI_API_KEY', 'test-not-real')
    original = httpx.Client
    with patch('app.main.httpx.Client', side_effect=lambda **kw: original(transport=httpx.MockTransport(lambda req: httpx.Response(status, json=payload)), **kw)):
        with pytest.raises(HTTPException) as error:
            summarize('Evidence', 'Main ideas')
    assert error.value.status_code == 502
    assert 'sensitive' not in error.value.detail
    assert 'test-not-real' not in error.value.detail


def test_gemini_timeout(monkeypatch):
    from app.main import summarize
    monkeypatch.setenv('GEMINI_API_KEY', 'test-not-real')
    with patch('app.main.httpx.Client') as sdk:
        sdk.return_value.__enter__.return_value.post.side_effect = httpx.ReadTimeout('timeout')
        with pytest.raises(HTTPException) as error:
            summarize('Evidence', 'Main ideas')
    assert error.value.status_code == 504


def test_any_public_domain(monkeypatch):
    monkeypatch.setenv("ALLOWED_DOMAINS", "example.com")
    with patch("app.main.socket.getaddrinfo", return_value=[(2, 1, 6, '', ('8.8.8.8', 443))]):
        assert validate_url("https://unlisted.example/page").hostname == "unlisted.example"


@pytest.mark.parametrize("address", ["10.0.0.1", "192.168.1.1", "169.254.169.254", "::1", "fc00::1"])
def test_private_addresses_blocked(address):
    with patch("app.main.socket.getaddrinfo", return_value=[(2, 1, 6, '', (address, 443))]):
        with pytest.raises(HTTPException) as error:
            validate_url("https://example.com")
    assert error.value.status_code == 403
