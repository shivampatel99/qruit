from __future__ import annotations

from app.connectors.sharepoint import SharePointChannel


class OneDriveChannel(SharePointChannel):
    """Same Graph drive API as SharePoint, rooted at /me/drive instead of a site.

    Selected by leaving SHAREPOINT_SITE_ID empty (see QRUIT_Connector_Dependencies.html §6).
    """

    name = "onedrive"
    display_name = "onedrive"

    def _drive_root(self) -> str:
        return "https://graph.microsoft.com/v1.0/me/drive"
