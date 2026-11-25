from decimal import Decimal
from typing import Any, Callable
import flet as ft

from app_client.api import ApiError
from app_client.state import AppState
from app_client.theme import BACKGROUND, BORDER, INK, MUTED, PRIMARY, SURFACE

DEFAULT_SUBCITIES = {
    "Bole": Decimal("180.00"),
    "Kazanchis": Decimal("180.00"),
    "Yeka": Decimal("200.00"),
    "Kirkos": Decimal("200.00"),
    "Bole Bulbula": Decimal("220.00"),
    "Nifas Silk-Lafto": Decimal("230.00"),
    "Arada": Decimal("220.00"),
    "Lideta": Decimal("220.00"),
    "Lemi Kura": Decimal("250.00"),
    "Kolfe Keranio": Decimal("260.00"),
    "Gulele": Decimal("260.00"),
    "Akaky Kaliti": Decimal("280.00"),
}


def build_checkout_view(
    state: AppState,
    on_cart_updated: Callable[[], None],
    on_order_placed: Callable[[], None],
) -> ft.Control:
    if not state.cart:
        return ft.Container(
            expand=True,
            bgcolor=BACKGROUND,
            alignment=ft.Alignment(0, 0),
            content=ft.Text(
                "Your Lijnet cart is empty. Add items in the Shop tab.",
                color=MUTED,
                size=14,
                weight=ft.FontWeight.W_600,
            ),
        )

    subcity_fees: dict[str, Decimal] = dict(DEFAULT_SUBCITIES)
    free_threshold = {"value": Decimal("4000.00")}
    cod_limit = {"value": Decimal("5000.00")}

    try:
        cfg = state.api.get_delivery_config()
        subcity_fees = {k: Decimal(str(v)) for k, v in cfg["sub_city_fees_etb"].items()}
        free_threshold["value"] = Decimal(str(cfg["free_delivery_threshold_etb"]))
        cod_limit["value"] = Decimal(str(cfg["cod_max_limit_etb"]))
    except ApiError:
        pass

    recipient_field = ft.TextField(
        label=state.t["recipient"],
        value=(state.user or {}).get("full_name", ""),
        border_radius=10,
    )
    phone_field = ft.TextField(
        label=state.t["phone"],
        value=(state.user or {}).get("phone_number", ""),
        border_radius=10,
    )
    woreda_field = ft.TextField(label=state.t["woreda"], value="03", border_radius=10)
    landmark_field = ft.TextField(
        label=state.t["landmark"],
        hint_text="Near Edna Mall, behind Friendship Building, blue gate",
        multiline=True,
        min_lines=2,
        border_radius=10,
    )
    gps_field = ft.TextField(
        label=state.t["gps_pin"],
        hint_text="8.9974,38.7867",
        border_radius=10,
    )

    payment_group = ft.RadioGroup(
        value="CHAPA",
        content=ft.Column(
            spacing=4,
            controls=[
                ft.Radio(value="CHAPA", label="Chapa (Telebirr / CBE Birr / Visa & Mastercard)"),
                ft.Radio(value="TELEBIRR", label="Telebirr Direct Merchant"),
                ft.Radio(value="SANTIMPAY", label="SantimPay Gateway"),
                ft.Radio(value="CBE_TRANSFER", label="CBE Direct Bank Transfer"),
                ft.Radio(value="COD", label="Cash on Delivery (Max 5,000 ETB)"),
            ],
        ),
    )

    subtotal_text = ft.Text(weight=ft.FontWeight.BOLD, color=INK)
    fee_text = ft.Text(weight=ft.FontWeight.BOLD, color=PRIMARY)
    total_text = ft.Text(size=16, weight=ft.FontWeight.W_800, color=PRIMARY)
    cart_items_col = ft.Column(spacing=8)

    def refresh_totals() -> tuple[Decimal, Decimal]:
        sub = state.cart_subtotal
        is_free = sub >= free_threshold["value"] and sub > Decimal("0.00")
        fee = (
            Decimal("0.00")
            if (sub <= Decimal("0.00") or is_free)
            else subcity_fees.get(state.selected_sub_city, Decimal("200.00"))
        )
        grand = sub + fee

        subtotal_text.value = f"{sub:,.2f} ETB"
        fee_text.value = (
            state.t["free_delivery"] if is_free else f"{fee:,.2f} ETB ({state.selected_sub_city})"
        )
        total_text.value = f"{grand:,.2f} ETB"
        return fee, grand

    def render_cart_items() -> None:
        cart_items_col.controls.clear()
        for pid, entry in list(state.cart.items()):
            prod = entry["product"]
            qty = entry["quantity"]
            title = prod["title_am"] if state.lang == "am" else prod["title_en"]
            price = Decimal(str(prod["price_etb"]))

            def change_qty(delta: int, p_id: int = pid, cur_q: int = qty) -> None:
                state.update_cart_qty(p_id, cur_q + delta)
                on_cart_updated()

            cart_items_col.controls.append(
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    controls=[
                        ft.Column(
                            expand=True,
                            spacing=2,
                            controls=[
                                ft.Text(title, size=13, weight=ft.FontWeight.BOLD, color=INK),
                                ft.Text(f"{price:,.2f} ETB × {qty}", size=11, color=MUTED),
                            ],
                        ),
                        ft.Row(
                            spacing=4,
                            controls=[
                                ft.OutlinedButton(
                                    content=ft.Text("-"),
                                    on_click=lambda e, fn=change_qty: fn(-1),
                                ),
                                ft.Text(str(qty), weight=ft.FontWeight.BOLD),
                                ft.OutlinedButton(
                                    content=ft.Text("+"),
                                    on_click=lambda e, fn=change_qty: fn(1),
                                ),
                            ],
                        ),
                    ],
                )
            )

    def on_subcity_change(e) -> None:
        val = getattr(e.control, "value", None)
        if val:
            state.selected_sub_city = val
            refresh_totals()
            state.page.update()

    subcity_dropdown = ft.Dropdown(
        label=state.t["sub_city"],
        value=state.selected_sub_city,
        options=[ft.DropdownOption(key=sc, text=sc) for sc in subcity_fees.keys()],
        on_select=on_subcity_change,
        border_radius=10,
    )

    submit_btn = ft.Button(
        content=ft.Text(state.t["place_order"], color="#FFFFFF", weight=ft.FontWeight.BOLD),
        bgcolor=PRIMARY,
        height=48,
    )

    def handle_submit_order(e) -> None:
        _, grand_total = refresh_totals()
        method = payment_group.value or "CHAPA"

        if len((landmark_field.value or "").strip()) < 8:
            state.show_toast(
                "Please enter a detailed Addis Ababa landmark (min 8 characters).", is_error=True
            )
            return

        if method == "COD" and grand_total > cod_limit["value"]:
            state.show_toast(
                f"Cash on Delivery is capped at {cod_limit['value']:,.0f} ETB.", is_error=True
            )
            return

        payload: dict[str, Any] = {
            "items": [
                {"product_id": pid, "quantity": entry["quantity"]}
                for pid, entry in state.cart.items()
            ],
            "address": {
                "recipient_name": (recipient_field.value or "").strip(),
                "sub_city": state.selected_sub_city,
                "woreda": (woreda_field.value or "").strip(),
                "landmark_description": (landmark_field.value or "").strip(),
                "gps_coordinates": (gps_field.value or "").strip() or None,
                "recipient_phone": (phone_field.value or "").strip(),
            },
            "payment_method": method,
        }

        submit_btn.disabled = True
        state.page.update()
        try:
            created = state.api.create_order(payload)
            state.clear_cart()
            note = created.get("payment_instructions") or f"Order {created['order_number']} confirmed!"
            state.show_toast(note)
            on_order_placed()
        except ApiError as err:
            state.show_toast(err.message, is_error=True)
        finally:
            submit_btn.disabled = False
            state.page.update()

    submit_btn.on_click = handle_submit_order
    render_cart_items()
    refresh_totals()

    return ft.Container(
        expand=True,
        bgcolor=BACKGROUND,
        padding=12,
        content=ft.Column(
            scroll=ft.ScrollMode.AUTO,
            spacing=12,
            controls=[
                ft.Container(
                    bgcolor=SURFACE,
                    padding=14,
                    border_radius=14,
                    border=ft.Border.all(1, BORDER),
                    content=ft.Column(
                        spacing=8,
                        controls=[
                            ft.Text("Cart Summary", size=15, weight=ft.FontWeight.BOLD, color=PRIMARY),
                            cart_items_col,
                        ],
                    ),
                ),
                ft.Container(
                    bgcolor=SURFACE,
                    padding=14,
                    border_radius=14,
                    border=ft.Border.all(1, BORDER),
                    content=ft.Column(
                        spacing=10,
                        controls=[
                            ft.Text(
                                state.t["checkout_title"],
                                size=15,
                                weight=ft.FontWeight.BOLD,
                                color=PRIMARY,
                            ),
                            recipient_field,
                            phone_field,
                            subcity_dropdown,
                            woreda_field,
                            landmark_field,
                            gps_field,
                        ],
                    ),
                ),
                ft.Container(
                    bgcolor=SURFACE,
                    padding=14,
                    border_radius=14,
                    border=ft.Border.all(1, BORDER),
                    content=ft.Column(
                        spacing=8,
                        controls=[
                            ft.Text(
                                state.t["payment_method"],
                                size=15,
                                weight=ft.FontWeight.BOLD,
                                color=PRIMARY,
                            ),
                            payment_group,
                        ],
                    ),
                ),
                ft.Container(
                    bgcolor=SURFACE,
                    padding=14,
                    border_radius=14,
                    border=ft.Border.all(1, BORDER),
                    content=ft.Column(
                        spacing=8,
                        controls=[
                            ft.Row(
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                controls=[ft.Text(state.t["subtotal"], color=MUTED), subtotal_text],
                            ),
                            ft.Row(
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                controls=[ft.Text(state.t["delivery_fee"], color=MUTED), fee_text],
                            ),
                            ft.Divider(height=1, color=BORDER),
                            ft.Row(
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                controls=[
                                    ft.Text(state.t["total"], size=16, weight=ft.FontWeight.BOLD),
                                    total_text,
                                ],
                            ),
                            submit_btn,
                        ],
                    ),
                ),
            ],
        ),
    )
