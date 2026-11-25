import flet as ft

from app_client.api import ApiError
from app_client.state import AppState
from app_client.theme import ACCENT, BACKGROUND, FONTS, PRIMARY, SECONDARY
from app_client.views.checkout_view import build_checkout_view
from app_client.views.login_view import build_login_view
from app_client.views.orders_view import build_orders_view
from app_client.views.shop_view import build_shop_view


def main(page: ft.Page) -> None:
    page.title = "Lijnet (ልጅነት) — Addis Ababa Kids Store"
    page.bgcolor = BACKGROUND
    page.fonts = FONTS
    page.theme = ft.Theme(font_family="NotoEthiopic")
    page.window.width = 430
    page.window.height = 840
    page.padding = 0

    state = AppState(page)
    current_tab = {"index": 0}

    if state.api.access_token:
        try:
            state.user = state.api.get_me()
        except ApiError:
            state.api.clear_tokens()
            state.user = None

    def switch_lang(code: str) -> None:
        state.lang = code
        render_app()

    def handle_logout(e) -> None:
        state.api.clear_tokens()
        state.user = None
        state.clear_cart()
        current_tab["index"] = 0
        render_app()

    def on_nav_change(e) -> None:
        current_tab["index"] = int(e.control.selected_index)
        render_app()

    def go_to_orders() -> None:
        current_tab["index"] = 2
        render_app()

    def render_app() -> None:
        page.controls.clear()

        if not state.user:
            page.navigation_bar = None
            page.add(build_login_view(state, on_authenticated=render_app))
            page.update()
            return

        top_bar = ft.Container(
            bgcolor=PRIMARY,
            padding=ft.Padding.symmetric(horizontal=14, vertical=10),
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                controls=[
                    ft.Row(
                        spacing=8,
                        controls=[
                            ft.Container(
                                width=32,
                                height=32,
                                border_radius=8,
                                bgcolor="#125563",
                                alignment=ft.Alignment(0, 0),
                                content=ft.Text(
                                    "ል",
                                    size=18,
                                    weight=ft.FontWeight.W_800,
                                    color=SECONDARY,
                                ),
                            ),
                            ft.Text(
                                f"{state.t['brand']} ({state.t['brand_am']})",
                                size=16,
                                weight=ft.FontWeight.W_800,
                                color="#FFFFFF",
                            ),
                        ],
                    ),
                    ft.Row(
                        spacing=6,
                        controls=[
                            ft.TextButton(
                                content=ft.Text(
                                    "EN",
                                    size=12,
                                    weight=ft.FontWeight.BOLD,
                                    color=SECONDARY if state.lang == "en" else "#FFFFFF",
                                ),
                                on_click=lambda e: switch_lang("en"),
                            ),
                            ft.TextButton(
                                content=ft.Text(
                                    "አማ",
                                    size=12,
                                    weight=ft.FontWeight.BOLD,
                                    color=SECONDARY if state.lang == "am" else "#FFFFFF",
                                ),
                                on_click=lambda e: switch_lang("am"),
                            ),
                            ft.TextButton(
                                content=ft.Text(
                                    state.t["sign_out"],
                                    size=11,
                                    weight=ft.FontWeight.BOLD,
                                    color=ACCENT,
                                ),
                                on_click=handle_logout,
                            ),
                        ],
                    ),
                ],
            ),
        )

        cart_label = (
            f"{state.t['tab_checkout']} ({state.cart_count})"
            if state.cart_count > 0
            else state.t["tab_checkout"]
        )
        page.navigation_bar = ft.NavigationBar(
            selected_index=current_tab["index"],
            on_change=on_nav_change,
            destinations=[
                ft.NavigationBarDestination(
                    icon=ft.Icons.STOREFRONT_OUTLINED,
                    selected_icon=ft.Icons.STOREFRONT,
                    label=state.t["tab_shop"],
                ),
                ft.NavigationBarDestination(
                    icon=ft.Icons.SHOPPING_CART_OUTLINED,
                    selected_icon=ft.Icons.SHOPPING_CART,
                    label=cart_label,
                ),
                ft.NavigationBarDestination(
                    icon=ft.Icons.RECEIPT_LONG_OUTLINED,
                    selected_icon=ft.Icons.RECEIPT_LONG,
                    label=state.t["tab_orders"],
                ),
            ],
        )

        idx = current_tab["index"]
        if idx == 0:
            body = build_shop_view(state, on_cart_updated=render_app)
        elif idx == 1:
            body = build_checkout_view(
                state,
                on_cart_updated=render_app,
                on_order_placed=go_to_orders,
            )
        else:
            body = build_orders_view(state)

        page.add(ft.Column(expand=True, spacing=0, controls=[top_bar, body]))
        page.update()

    render_app()


if __name__ == "__main__":
    ft.run(main)
