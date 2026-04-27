"""
dashboard/dashboard.py — Reflex status dashboard for SecureCommit.

Design aesthetic: security operations terminal — deep black background,
phosphor green primary text, monospace typography, crisp tabular data.
Think Bloomberg terminal meets modern dark UI.

Architecture note: Reflex is a Python-native full-stack framework.  State
classes handle data fetching; components are pure functions of state.
We fetch from the FastAPI /api/sessions endpoint rather than hitting Postgres
directly, keeping the dashboard decoupled from the DB schema.
"""

import os

import reflex as rx
import httpx
from datetime import datetime
from typing import Optional


# ── Palette ───────────────────────────────────────────────────────────────────

TERMINAL_BG = "#0a0a0a"
TERMINAL_BG2 = "#111111"
TERMINAL_BORDER = "#1e1e1e"
GREEN_PRIMARY = "#00ff88"
GREEN_DIM = "#00cc66"
GREEN_FAINT = "#003322"
AMBER = "#ffb300"
RED = "#ff4444"
BLUE = "#4488ff"
GRAY = "#888888"
TEXT = "#e0e0e0"
FONT_MONO = "'JetBrains Mono', 'Fira Code', 'Courier New', monospace"


SEVERITY_COLOURS = {
    "Critical": RED,
    "High": AMBER,
    "Medium": BLUE,
    "Low": GRAY,
}


# ── State ─────────────────────────────────────────────────────────────────────

class Session(rx.Base):
    id: str = ""
    repo_full_name: str = ""
    pr_number: int = 0
    pr_title: str = ""
    pr_url: str = ""
    head_sha: str = ""
    status: str = "pending"
    created_at: str = ""
    findings: list[dict] = []

    @property
    def finding_count(self) -> int:
        return len(self.findings)

    @property
    def worst_severity(self) -> str:
        order = ["Critical", "High", "Medium", "Low"]
        for sev in order:
            if any(f.get("severity") == sev for f in self.findings):
                return sev
        return "—"


class DashboardState(rx.State):
    sessions: list[Session] = []
    selected_session_id: str = ""
    is_loading: bool = False
    error: str = ""
    api_base: str = os.getenv("API_BASE", "http://localhost:8000")

    @rx.var
    def selected_session(self) -> Optional[Session]:
        for s in self.sessions:
            if s.id == self.selected_session_id:
                return s
        return None

    @rx.var
    def total_findings(self) -> int:
        return sum(len(s.findings) for s in self.sessions)

    @rx.var
    def critical_count(self) -> int:
        return sum(
            1
            for s in self.sessions
            for f in s.findings
            if f.get("severity") == "Critical"
        )

    @rx.var
    def repos_monitored(self) -> int:
        return len({s.repo_full_name for s in self.sessions})

    async def load_sessions(self):
        self.is_loading = True
        self.error = ""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.api_base}/api/sessions")
                resp.raise_for_status()
                data = resp.json()
                self.sessions = [Session(**s) for s in data]
        except Exception as e:
            self.error = f"Failed to load sessions: {e}"
        finally:
            self.is_loading = False

    def select_session(self, session_id: str):
        self.selected_session_id = session_id


# ── Components ────────────────────────────────────────────────────────────────

def severity_badge(severity) -> rx.Component:
    colour = rx.match(
        severity,
        ("Critical", RED),
        ("High", AMBER),
        ("Medium", BLUE),
        GRAY,
    )
    return rx.box(
        rx.text(severity, font_family=FONT_MONO, font_size="11px", font_weight="700"),
        background=rx.match(
            severity,
            ("Critical", f"{RED}22"),
            ("High", f"{AMBER}22"),
            ("Medium", f"{BLUE}22"),
            f"{GRAY}22",
        ),
        border=rx.match(
            severity,
            ("Critical", f"1px solid {RED}"),
            ("High", f"1px solid {AMBER}"),
            ("Medium", f"1px solid {BLUE}"),
            f"1px solid {GRAY}",
        ),
        color=colour,
        padding="2px 8px",
        border_radius="3px",
        display="inline-block",
    )


def status_dot(status) -> rx.Component:
    colour = rx.match(
        status,
        ("completed", GREEN_PRIMARY),
        ("processing", AMBER),
        ("pending", GRAY),
        ("failed", RED),
        GRAY,
    )
    return rx.box(
        width="8px",
        height="8px",
        border_radius="50%",
        background=colour,
        display="inline-block",
        margin_right="8px",
        flex_shrink="0",
    )


def stat_card(label: str, value: rx.Var, accent: str = GREEN_PRIMARY) -> rx.Component:
    return rx.box(
        rx.text(
            label,
            font_family=FONT_MONO,
            font_size="10px",
            color=GRAY,
            text_transform="uppercase",
            letter_spacing="0.12em",
            margin_bottom="8px",
        ),
        rx.text(
            value,
            font_family=FONT_MONO,
            font_size="32px",
            font_weight="700",
            color=accent,
            line_height="1",
        ),
        background=TERMINAL_BG2,
        border=f"1px solid {TERMINAL_BORDER}",
        border_top=f"2px solid {accent}",
        padding="20px 24px",
        border_radius="4px",
        flex="1",
        min_width="140px",
    )


def _sev_match(sev, critical, high, medium, default):
    return rx.match(sev, ("Critical", critical), ("High", high), ("Medium", medium), default)


def finding_row(finding: dict) -> rx.Component:
    sev = finding.get("severity", "Low")
    return rx.box(
        rx.hstack(
            severity_badge(sev),
            rx.box(
                rx.text(
                    finding.get("file_path", ""),
                    font_family=FONT_MONO,
                    font_size="12px",
                    color=GREEN_DIM,
                    font_weight="600",
                ),
                rx.text(
                    "Line ",
                    finding.get("diff_line_number", "?"),
                    " · ",
                    finding.get("category", ""),
                    font_family=FONT_MONO,
                    font_size="11px",
                    color=GRAY,
                ),
                flex="1",
            ),
            align="start",
            spacing="3",
        ),
        rx.text(
            finding.get("explanation", ""),
            font_family=FONT_MONO,
            font_size="12px",
            color=TEXT,
            margin_top="10px",
            line_height="1.6",
        ),
        rx.box(
            rx.text(
                "SUGGESTED FIX",
                font_family=FONT_MONO,
                font_size="10px",
                color=GRAY,
                font_weight="700",
                letter_spacing="0.1em",
                margin_bottom="6px",
            ),
            rx.code_block(
                finding.get("suggested_fix", ""),
                language="python",
                font_size="11px",
                background=TERMINAL_BG,
                border=f"1px solid {TERMINAL_BORDER}",
                border_radius="3px",
                padding="10px",
                width="100%",
            ),
            margin_top="12px",
        ),
        background=TERMINAL_BG2,
        border=f"1px solid {TERMINAL_BORDER}",
        border_left=_sev_match(sev, f"3px solid {RED}", f"3px solid {AMBER}", f"3px solid {BLUE}", f"3px solid {GRAY}"),
        padding="16px 20px",
        border_radius="4px",
        margin_bottom="8px",
    )


def session_row(session: Session) -> rx.Component:
    return rx.box(
        rx.hstack(
            status_dot(session.status),
            rx.vstack(
                rx.hstack(
                    rx.text(
                        session.repo_full_name,
                        font_family=FONT_MONO,
                        font_size="13px",
                        font_weight="700",
                        color=GREEN_PRIMARY,
                    ),
                    rx.text(
                        f"#{session.pr_number}",
                        font_family=FONT_MONO,
                        font_size="13px",
                        color=GRAY,
                    ),
                    spacing="1",
                ),
                rx.text(
                    session.pr_title,
                    font_family=FONT_MONO,
                    font_size="11px",
                    color=TEXT,
                    no_of_lines=1,
                ),
                align="start",
                spacing="1",
                flex="1",
            ),
            rx.vstack(
                rx.text(
                    session.finding_count,
                    " finding(s)",
                    font_family=FONT_MONO,
                    font_size="11px",
                    color=GRAY,
                    text_align="right",
                ),
                rx.cond(
                    session.finding_count > 0,
                    severity_badge(session.worst_severity),
                    rx.box(),
                ),
                align="end",
                spacing="1",
            ),
            spacing="3",
            width="100%",
        ),
        background=rx.cond(
            DashboardState.selected_session_id == session.id,
            GREEN_FAINT,
            TERMINAL_BG2,
        ),
        border=rx.cond(
            DashboardState.selected_session_id == session.id,
            f"1px solid {GREEN_DIM}",
            f"1px solid {TERMINAL_BORDER}",
        ),
        padding="14px 18px",
        border_radius="4px",
        cursor="pointer",
        on_click=DashboardState.select_session(session.id),
        _hover={"background": GREEN_FAINT, "border_color": GREEN_DIM},
        transition="all 0.15s ease",
        margin_bottom="4px",
    )


def detail_panel() -> rx.Component:
    return rx.cond(
        DashboardState.selected_session_id != "",
        rx.box(
            rx.hstack(
                rx.vstack(
                    rx.link(
                        rx.text(
                            DashboardState.selected_session.repo_full_name
                            + " #"
                            + DashboardState.selected_session.pr_number.to_string(),
                            font_family=FONT_MONO,
                            font_size="16px",
                            font_weight="700",
                            color=GREEN_PRIMARY,
                        ),
                        href=DashboardState.selected_session.pr_url,
                        is_external=True,
                    ),
                    rx.text(
                        DashboardState.selected_session.pr_title,
                        font_family=FONT_MONO,
                        font_size="13px",
                        color=TEXT,
                    ),
                    rx.text(
                        "SHA: " + DashboardState.selected_session.head_sha,
                        font_family=FONT_MONO,
                        font_size="11px",
                        color=GRAY,
                    ),
                    align="start",
                    spacing="1",
                ),
                rx.spacer(),
                status_dot(DashboardState.selected_session.status),
                rx.text(
                    DashboardState.selected_session.status.upper(),
                    font_family=FONT_MONO,
                    font_size="12px",
                    font_weight="700",
                    color=GRAY,
                    letter_spacing="0.1em",
                ),
                align="center",
            ),
            rx.divider(border_color=TERMINAL_BORDER, margin_y="20px"),
            rx.cond(
                DashboardState.selected_session.findings.length() == 0,
                rx.box(
                    rx.text(
                        "[ NO FINDINGS — REVIEW CLEAN ]",
                        font_family=FONT_MONO,
                        font_size="14px",
                        color=GREEN_PRIMARY,
                        text_align="center",
                    ),
                    padding="40px",
                    text_align="center",
                ),
                rx.vstack(
                    rx.foreach(
                        DashboardState.selected_session.findings,
                        finding_row,
                    ),
                    width="100%",
                    spacing="0",
                ),
            ),
            width="100%",
        ),
        rx.box(
            rx.text(
                "← SELECT A REVIEW SESSION",
                font_family=FONT_MONO,
                font_size="13px",
                color=GRAY,
                letter_spacing="0.1em",
            ),
            display="flex",
            align_items="center",
            justify_content="center",
            height="300px",
        ),
    )


def index() -> rx.Component:
    return rx.box(
        # ── Top bar ──────────────────────────────────────────────────────────
        rx.box(
            rx.hstack(
                rx.hstack(
                    rx.box(
                        rx.text(
                            "●",
                            color=GREEN_PRIMARY,
                            font_size="20px",
                            margin_right="2px",
                        ),
                        rx.text(
                            "SECURE",
                            font_family=FONT_MONO,
                            font_size="16px",
                            font_weight="800",
                            color=GREEN_PRIMARY,
                            letter_spacing="0.08em",
                        ),
                        rx.text(
                            "COMMIT",
                            font_family=FONT_MONO,
                            font_size="16px",
                            font_weight="300",
                            color=TEXT,
                            letter_spacing="0.08em",
                        ),
                        display="flex",
                        align_items="center",
                        gap="4px",
                    ),
                    rx.text(
                        "/ SECURITY OPERATIONS DASHBOARD",
                        font_family=FONT_MONO,
                        font_size="11px",
                        color=GRAY,
                        letter_spacing="0.1em",
                    ),
                    spacing="3",
                    align="center",
                ),
                rx.spacer(),
                rx.button(
                    "⟳  REFRESH",
                    on_click=DashboardState.load_sessions,
                    font_family=FONT_MONO,
                    font_size="11px",
                    font_weight="700",
                    letter_spacing="0.1em",
                    color=GREEN_PRIMARY,
                    background="transparent",
                    border=f"1px solid {GREEN_DIM}",
                    padding="6px 16px",
                    border_radius="3px",
                    cursor="pointer",
                    _hover={"background": GREEN_FAINT},
                ),
                align="center",
                width="100%",
            ),
            background=TERMINAL_BG2,
            border_bottom=f"1px solid {TERMINAL_BORDER}",
            padding="14px 32px",
        ),

        # ── Stats row ─────────────────────────────────────────────────────────
        rx.hstack(
            stat_card("Reviews", DashboardState.sessions.length(), GREEN_PRIMARY),
            stat_card("Total Findings", DashboardState.total_findings, AMBER),
            stat_card("Critical", DashboardState.critical_count, RED),
            stat_card("Repos", DashboardState.repos_monitored, BLUE),
            spacing="3",
            padding="24px 32px 0 32px",
            width="100%",
            flex_wrap="wrap",
        ),

        # ── Error banner ──────────────────────────────────────────────────────
        rx.cond(
            DashboardState.error != "",
            rx.box(
                rx.text(
                    DashboardState.error,
                    font_family=FONT_MONO,
                    font_size="12px",
                    color=RED,
                ),
                background=f"{RED}11",
                border=f"1px solid {RED}",
                padding="12px 24px",
                margin="16px 32px 0 32px",
                border_radius="4px",
            ),
        ),

        # ── Two-column main layout ─────────────────────────────────────────
        rx.hstack(
            # Session list
            rx.box(
                rx.text(
                    "REVIEW SESSIONS",
                    font_family=FONT_MONO,
                    font_size="10px",
                    color=GRAY,
                    font_weight="700",
                    letter_spacing="0.15em",
                    margin_bottom="12px",
                ),
                rx.cond(
                    DashboardState.is_loading,
                    rx.box(
                        rx.text(
                            "LOADING...",
                            font_family=FONT_MONO,
                            font_size="12px",
                            color=GREEN_DIM,
                        ),
                        padding="20px 0",
                    ),
                    rx.foreach(DashboardState.sessions, session_row),
                ),
                width="360px",
                flex_shrink="0",
                overflow_y="auto",
                max_height="calc(100vh - 260px)",
            ),

            # Divider
            rx.box(width="1px", background=TERMINAL_BORDER, align_self="stretch"),

            # Detail panel
            rx.box(
                detail_panel(),
                flex="1",
                overflow_y="auto",
                max_height="calc(100vh - 260px)",
                padding_left="24px",
            ),
            spacing="0",
            padding="24px 32px",
            gap="24px",
            align="start",
            width="100%",
        ),

        background=TERMINAL_BG,
        min_height="100vh",
        on_mount=DashboardState.load_sessions,
    )


# ── App ───────────────────────────────────────────────────────────────────────

app = rx.App(
    style={
        "background": TERMINAL_BG,
        "color": TEXT,
        "font_family": FONT_MONO,
    }
)
app.add_page(index, route="/", title="SecureCommit Dashboard")
