from typing import Callable
import flet as ft

from app_client.api import ApiError
from app_client.state import AppState
from app_client.theme import BACKGROUND, BORDER, MUTED, PRIMARY, SECONDARY, SURFACE


def build_login_view(state: AppState, on_authenticated: Callable[[], None]) -> ft.Control:
    is_register = {"value": False}

    full_name_field = ft.TextField(
        label="Full Name",
        hint_text="Selamawit Bekele",
        visible=False,
        border_radius=10,
    )
    email_field = ft.TextField(
        label="Email Address",
        hint_text="selam@example.et",
        visible=False,
        border_radius=10,
    )
    identifier_field = ft.TextField(
        label="Email or Ethiopian Phone (+2519... / 09...)",
        value="0913445566",
        border_radius=10,
    )
    password_field = ft.TextField(
        label="Password",
        value="CustomerPassword123!",
        password=True,
        can_reveal_password=True,
        border_radius=10,
    )
    submit_btn = ft.Button(
        content=ft.Text("Sign In", color="#FFFFFF", weight=ft.FontWeight.BOLD),
        bgcolor=PRIMARY,
        height=46,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10)),
    )
    toggle_btn = ft.TextButton(
        content=ft.Text("New to Lijnet? Create a Parent Account", color=PRIMARY)
    )

    def handle_submit(e) -> None:
        submit_btn.disabled = True
        state.page.update()
        try:
            if is_register["value"]:
                res = state.api.register(
                    full_name=full_name_field.value or "",
                    email=email_field.value or "",
                    phone_number=identifier_field.value or "",
                    password=password_field.value or "",
                )
            else:
                res = state.api.login(
                    identifier=identifier_field.value or "",
                    password=password_field.value or "",
                )
            state.user = res["user"]
            state.show_toast(f"Welcome, {state.user['full_name']}!")
            on_authenticated()
        except ApiError as err:
            state.show_toast(err.message, is_error=True)
        finally:
            submit_btn.disabled = False
            state.page.update()

    def toggle_mode(e) -> None:
        is_register["value"] = not is_register["value"]
        reg = is_register["value"]
        full_name_field.visible = reg
        email_field.visible = reg
        identifier_field.label = (
            "Ethiopian Phone (+2519... / 09...)" if reg else "Email or Ethiopian Phone"
        )
        submit_btn.content = ft.Text(
            "Create Account" if reg else "Sign In",
            color="#FFFFFF",
            weight=ft.FontWeight.BOLD,
        )
        toggle_btn.content = ft.Text(
            "Already have an account? Sign In"
            if reg
            else "New to Lijnet? Create a Parent Account",
            color=PRIMARY,
        )
        state.page.update()

    submit_btn.on_click = handle_submit
    toggle_btn.on_click = toggle_mode

    return ft.Container(
        expand=True,
        bgcolor=BACKGROUND,
        padding=20,
        alignment=ft.Alignment(0, 0),
        content=ft.Column(
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            scroll=ft.ScrollMode.AUTO,
            controls=[
                ft.Container(
                    width=64,
                    height=64,
                    border_radius=16,
                    bgcolor=PRIMARY,
                    alignment=ft.Alignment(0, 0),
                    content=ft.Text("ል", size=32, weight=ft.FontWeight.W_800, color=SECONDARY),
                ),
                ft.Row(
                    alignment=ft.MainAxisAlignment.CENTER,
                    controls=[
                        ft.Text("Lijnet", size=24, weight=ft.FontWeight.W_800, color=PRIMARY),
                        ft.Text("ልጅነት", size=24, weight=ft.FontWeight.W_800, color=SECONDARY),
                    ],
                ),
                ft.Text(state.t["tagline"], size=12, color=MUTED, text_align=ft.TextAlign.CENTER),
                ft.Container(height=12),
                ft.Container(
                    width=420,
                    bgcolor=SURFACE,
                    padding=20,
                    border_radius=16,
                    border=ft.Border.all(1, BORDER),
                    content=ft.Column(
                        spacing=12,
                        controls=[
                            full_name_field,
                            email_field,
                            identifier_field,
                            password_field,
                            ft.Container(width=380, content=submit_btn),
                            ft.Row(alignment=ft.MainAxisAlignment.CENTER, controls=[toggle_btn]),
                        ],
                    ),
                ),
            ],
        ),
    )
