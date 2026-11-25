from decimal import Decimal
from typing import Any
import flet as ft

from app_client.api import ApiError
from app_client.state import AppState
from app_client.theme import ACCENT, BACKGROUND, BORDER, ECO, INK, MUTED, PRIMARY, SURFACE


def build_orders_view(state: AppState) -> ft.Control:
    orders_col = ft.Column(spacing=12, scroll=ft.ScrollMode.AUTO, expand=True)

    def open_return_dialog(order: dict[str, Any]) -> None:
        reason_field = ft.TextField(
            label="Return / Exchange Reason",
            hint_text="e.g., Wrong size, exchange requested",
            multiline=True,
            min_lines=2,
        )
        hygiene_switch = ft.Switch(
            label="Was a factory hygiene seal opened?",
            value=False,
        )

        def close_dlg(e=None) -> None:
            if hasattr(state.page, "pop_dialog"):
                state.page.pop_dialog()
            else:
                dlg.open = False
                state.page.update()

        def submit_return(e) -> None:
            try:
                state.api.request_return(
                    order_id=int(order["id"]),
                    reason=(reason_field.value or "").strip(),
                    hygiene_seal_broken=bool(hygiene_switch.value),
                )
                close_dlg()
                state.show_toast(
                    "Return approved under Ethiopia's 7-day Proclamation No. 1205/2020 window."
                )
                load_orders()
            except ApiError as err:
                state.show_toast(err.message, is_error=True)

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"Return {order['order_number']}"),
            content=ft.Column(
                tight=True,
                spacing=10,
                controls=[
                    ft.Text(
                        "Proclamation No. 1205/2020 allows returns within 7 calendar days of delivery "
                        "for unopened items.",
                        size=12,
                        color=MUTED,
                    ),
                    reason_field,
                    hygiene_switch,
                ],
            ),
            actions=[
                ft.TextButton(content=ft.Text("Cancel"), on_click=close_dlg),
                ft.Button(
                    content=ft.Text("Submit Return", color="#FFFFFF", weight=ft.FontWeight.BOLD),
                    bgcolor=ACCENT,
                    on_click=submit_return,
                ),
            ],
        )

        if hasattr(state.page, "show_dialog"):
            state.page.show_dialog(dlg)
        else:
            state.page.overlay.append(dlg)
            dlg.open = True
            state.page.update()

    def render_order_card(order: dict[str, Any]) -> ft.Control:
        addr = order["delivery_address"]
        total_val = Decimal(str(order["total_etb"]))
        eligible = bool(order.get("return_eligible"))

        item_lines = [
            ft.Text(
                f"• {line['quantity']}× {line['title_am'] if state.lang == 'am' else line['title_en']}",
                size=12,
                color=INK,
            )
            for line in order.get("items", [])
        ]

        controls: list[ft.Control] = [
            ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                controls=[
                    ft.Text(
                        order["order_number"],
                        size=15,
                        weight=ft.FontWeight.W_800,
                        color=PRIMARY,
                    ),
                    ft.Container(
                        bgcolor="#FEF3C7",
                        padding=ft.Padding.symmetric(horizontal=10, vertical=4),
                        border_radius=12,
                        content=ft.Text(
                            f"{order['status']} • {order['payment_status']}",
                            size=11,
                            weight=ft.FontWeight.BOLD,
                            color=INK,
                        ),
                    ),
                ],
            ),
            ft.Text(
                f"{order['payment_method']} | Total: {total_val:,.2f} ETB",
                size=12,
                weight=ft.FontWeight.W_600,
                color=INK,
            ),
            ft.Text(
                f"📍 {addr['sub_city']} (Woreda {addr['woreda']}) — {addr['landmark_description']}",
                size=12,
                color=MUTED,
            ),
            ft.Column(spacing=2, controls=item_lines),
            ft.Text(
                f"📜 {order.get('return_note', '')}",
                size=11,
                weight=ft.FontWeight.W_600,
                color=ECO if eligible else MUTED,
            ),
        ]

        if eligible:
            controls.append(
                ft.Button(
                    content=ft.Text(
                        state.t["request_return"],
                        size=12,
                        weight=ft.FontWeight.BOLD,
                        color="#FFFFFF",
                    ),
                    bgcolor=ACCENT,
                    on_click=lambda e, o=order: open_return_dialog(o),
                )
            )

        return ft.Container(
            bgcolor=SURFACE,
            border_radius=14,
            border=ft.Border.all(1, BORDER),
            padding=14,
            content=ft.Column(spacing=8, controls=controls),
        )

    def load_orders() -> None:
        orders_col.controls.clear()
        orders_col.controls.append(
            ft.Container(
                padding=24,
                alignment=ft.Alignment(0, 0),
                content=ft.ProgressRing(color=PRIMARY),
            )
        )
        state.page.update()

        try:
            orders = state.api.get_orders()
            orders_col.controls.clear()
            if not orders:
                orders_col.controls.append(
                    ft.Text("No orders placed yet.", color=MUTED, size=14)
                )
            else:
                for o in orders:
                    orders_col.controls.append(render_order_card(o))
        except ApiError as err:
            orders_col.controls.clear()
            state.show_toast(err.message, is_error=True)
        state.page.update()

    load_orders()

    return ft.Container(
        expand=True,
        bgcolor=BACKGROUND,
        padding=12,
        content=ft.Column(
            expand=True,
            spacing=10,
            controls=[
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    controls=[
                        ft.Text(
                            state.t["orders_title"],
                            size=16,
                            weight=ft.FontWeight.BOLD,
                            color=PRIMARY,
                        ),
                        ft.OutlinedButton(
                            content=ft.Text("Refresh", size=12),
                            on_click=lambda e: load_orders(),
                        ),
                    ],
                ),
                orders_col,
            ],
        ),
    )
