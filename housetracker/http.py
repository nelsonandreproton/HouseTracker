import requests
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

USER_AGENT = "HouseTracker/1.0 (personal property monitor)"


def _should_retry(exc: BaseException) -> bool:
    """Never retry 429 (extends the ban) nor TLS errors (retrying cannot fix them)."""
    response = getattr(exc, "response", None)
    if response is not None and response.status_code == 429:
        return False
    return not isinstance(exc, requests.exceptions.SSLError)


@retry(
    retry=retry_if_exception(_should_retry),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
def request(session: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", 30)
    response = session.request(method, url, **kwargs)
    response.raise_for_status()
    return response


def get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    return request(session, "GET", url, **kwargs)


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    return session
