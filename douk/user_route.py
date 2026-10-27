"""Adds POST /tiktok/user to DouK's Web API, then starts DouK as usual.

DouK can look up a TikTok user by uniqueId (signed /api/user/detail/ call) but does not expose it
over HTTP. Plain profile page scraping gets a WAF challenge, so we reuse DouK's signed request.
"""

import runpy

from src.application.main_server import APIServer
from src.models.base import APIModel
from src.models.response import DataResponse

_original_setup_routes = APIServer.setup_routes


class UserTikTok(APIModel):
    unique_id: str


def _setup_routes_with_user(self: APIServer) -> None:
    _original_setup_routes(self)

    @self.server.post("/tiktok/user", response_model=DataResponse, tags=["TikTok"])
    async def handle_user_tiktok(extract: UserTikTok) -> DataResponse:
        data = await self.get_user_info_data(
            tiktok=True,
            cookie=extract.cookie,
            proxy=extract.proxy or None,
            unique_id=extract.unique_id,
        )
        return DataResponse(
            message="ok" if data else "failed",
            data=data or None,
            params={"unique_id": extract.unique_id},
        )


APIServer.setup_routes = _setup_routes_with_user  # type: ignore[method-assign]

if __name__ == "__main__":
    runpy.run_path("main.py", run_name="__main__")
