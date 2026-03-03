from django.urls import reverse_lazy

UNFOLD = {
    "SITE_TITLE": "My Project Admin",   # CHANGE THIS
    "SITE_HEADER": "My Project Admin",  # CHANGE THIS
    "SITE_URL": "/",
    "SITE_SYMBOL": "speed",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": True,
    "COLORS": {
        "primary": {
            "50": "250 245 255",
            "100": "243 232 255",
            "200": "233 213 255",
            "300": "216 180 254",
            "400": "192 132 252",
            "500": "168 85 247",
            "600": "147 51 234",
            "700": "126 34 206",
            "800": "107 33 168",
            "900": "88 28 135",
            "950": "59 7 100",
        },
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": True,
        "navigation": [
            {
                "title": "System",
                "separator": True,
                "items": [
                    {
                        "title": "Users",
                        "icon": "people",
                        "link": lambda request: reverse_lazy("admin:system_userbase_changelist"),
                    },
                    {
                        "title": "Verification codes",
                        "icon": "check",
                        "link": lambda request: reverse_lazy("admin:system_verificationcode_changelist"),
                    },
                ],
            },
        ],
    },
}
