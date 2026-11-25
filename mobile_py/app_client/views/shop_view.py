from decimal import Decimal
from typing import Any, Callable
import flet as ft

from app_client.api import ApiError
from app_client.state import AppState
from app_client.theme import ACCENT, BACKGROUND, BORDER, ECO, INK, MUTED, PRIMARY, SURFACE

AGE_FILTERS = ["0-6m", "6-12m", "1-3y", "4-7y", "8-12y"]


def build_shop_view(state: AppState, on_cart_updated: Callable[[], None]) -> ft.Control:
    selected_age: dict[str, str | None] = {"value": None}
    products_list = ft.Column(spacing=12, scroll=ft.ScrollMode.AUTO, expand=True)

    search_field = ft.TextField(
        hint_text=state.t["search_hint"],
        border_radius=10,
        bgcolor=SURFACE,
        height=44,
        content_padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        on_submit=lambda e: load_products(),
    )

    age_row = ft.Row(scroll=ft.ScrollMode.AUTO, spacing=6)

    def build_age_pills() -> None:
        age_row.controls.clear()
        all_active = selected_age["value"] is None
        age_row.controls.append(
            ft.Button(
                content=ft.Text(
                    state.t["all_ages"],
                    size=11,
                    weight=ft.FontWeight.BOLD,
                    color="#FFFFFF" if all_active else INK,
                ),
                bgcolor=PRIMARY if all_active else SURFACE,
                on_click=lambda e: select_age(None),
            )
        )
        for age in AGE_FILTERS:
            active = selected_age["value"] == age
            age_row.controls.append(
                ft.Button(
                    content=ft.Text(
                        age,
                        size=11,
                        weight=ft.FontWeight.BOLD,
                        color="#FFFFFF" if active else INK,
                    ),
                    bgcolor=PRIMARY if active else SURFACE,
                    on_click=lambda e, a=age: select_age(a),
                )
            )

    def select_age(age: str | None) -> None:
        selected_age["value"] = age
        build_age_pills()
        load_products()

    def handle_add_to_cart(product: dict[str, Any]) -> None:
        state.add_to_cart(product, qty=1)
        state.show_toast(f"Added: {product['title']}")
        on_cart_updated()

    def render_product_card(prod: dict[str, Any]) -> ft.Control:
        is_local = bool(prod.get("is_local"))
        price_val = Decimal(str(prod["price_etb"]))
        stock = int(prod.get("stock_count", 0))

        origin_badge = ft.Container(
            bgcolor=ECO if is_local else "#E2E8F0",
            padding=ft.Padding.symmetric(horizontal=8, vertical=3),
            border_radius=12,
            content=ft.Text(
                f"🇪🇹 {state.t['made_in_ethiopia']}" if is_local else state.t["verified_import"],
                size=10,
                weight=ft.FontWeight.BOLD,
                color="#FFFFFF" if is_local else INK,
            ),
        )
        age_badge = ft.Container(
            bgcolor="#FEF3C7",
            padding=ft.Padding.symmetric(horizontal=8, vertical=3),
            border_radius=12,
            content=ft.Text(prod["age_range"], size=10, weight=ft.FontWeight.BOLD, color=INK),
        )

        return ft.Container(
            bgcolor=SURFACE,
            border_radius=14,
            border=ft.Border.all(1, BORDER),
            padding=12,
            content=ft.Column(
                spacing=8,
                controls=[
                    ft.Image(
                        src=prod.get("image_url", ""),
                        height=150,
                        width=460,
                        fit=ft.BoxFit.COVER,
                        border_radius=10,
                    ),
                    ft.Row(spacing=6, controls=[origin_badge, age_badge]),
                    ft.Text(
                        prod["title"],
                        size=14,
                        weight=ft.FontWeight.BOLD,
                        color=INK,
                        max_lines=2,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                    ft.Text(
                        f"✓ {state.t['verified_safe']}",
                        size=11,
                        color=PRIMARY,
                        weight=ft.FontWeight.W_600,
                    ),
                    ft.Divider(height=1, color=BORDER),
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            ft.Column(
                                spacing=2,
                                controls=[
                                    ft.Text(
                                        f"{price_val:,.2f} ETB",
                                        size=15,
                                        weight=ft.FontWeight.W_800,
                                        color=INK,
                                    ),
                                    ft.Text(
                                        f"In Stock ({stock})" if stock > 0 else "Out of Stock",
                                        size=11,
                                        color=ECO if stock > 0 else ACCENT,
                                    ),
                                ],
                            ),
                            ft.Button(
                                content=ft.Text(
                                    f"+ {state.t['add_to_cart']}",
                                    size=12,
                                    weight=ft.FontWeight.BOLD,
                                    color="#FFFFFF",
                                ),
                                bgcolor=ACCENT,
                                disabled=(stock <= 0),
                                on_click=lambda e, p=prod: handle_add_to_cart(p),
                            ),
                        ],
                    ),
                ],
            ),
        )

    def load_products() -> None:
        products_list.controls.clear()
        products_list.controls.append(
            ft.Container(
                padding=24,
                alignment=ft.Alignment(0, 0),
                content=ft.ProgressRing(color=PRIMARY),
            )
        )
        state.page.update()

        try:
            items = state.api.get_products(
                lang=state.lang,
                age_range=selected_age["value"],
                q=search_field.value,
            )
            products_list.controls.clear()
            if not items:
                products_list.controls.append(
                    ft.Text("No products match your filter.", color=MUTED, size=13)
                )
            else:
                for p in items:
                    products_list.controls.append(render_product_card(p))
        except ApiError as err:
            products_list.controls.clear()
            state.show_toast(err.message, is_error=True)
        state.page.update()

    build_age_pills()
    load_products()

    return ft.Container(
        expand=True,
        bgcolor=BACKGROUND,
        padding=12,
        content=ft.Column(
            expand=True,
            spacing=10,
            controls=[
                ft.Row(
                    spacing=8,
                    controls=[
                        ft.Container(expand=True, content=search_field),
                        ft.Button(
                            content=ft.Text("Go", color="#FFFFFF", weight=ft.FontWeight.BOLD),
                            bgcolor=PRIMARY,
                            height=44,
                            on_click=lambda e: load_products(),
                        ),
                    ],
                ),
                age_row,
                products_list,
            ],
        ),
    )
