"""AI Assistant page - Direct Claude Code integration via message queue."""

import streamlit as st
import time


def send_message(prompt: str) -> int:
    """Send a message to Claude Code via database queue. Returns message ID."""
    from g2.ui.components.database import get_connection

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ui_messages (prompt, status) VALUES (%s, 'pending') RETURNING id",
                (prompt,)
            )
            msg_id = cur.fetchone()[0]
            conn.commit()
            return msg_id


def get_response(msg_id: int, timeout: int = 60) -> str | None:
    """Poll for response from Claude Code. Returns response or None if timeout."""
    from g2.ui.components.database import get_connection

    start = time.time()
    while time.time() - start < timeout:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT response, status FROM ui_messages WHERE id = %s",
                    (msg_id,)
                )
                row = cur.fetchone()
                if row and row[1] == 'responded':
                    return row[0]
        time.sleep(1)
    return None


def get_chat_history() -> list:
    """Get recent chat history from database."""
    from g2.ui.components.database import get_connection

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT prompt, response, status, created_at
                FROM ui_messages
                ORDER BY created_at DESC
                LIMIT 20
            """)
            rows = cur.fetchall()
            return list(reversed(rows))


def render_assistant():
    """Render the AI Assistant page with direct Claude Code integration."""
    st.title("🤖 AI Assistant")
    st.markdown("Chat directly with Claude Code through this interface.")

    st.success("""
    💬 **Direct Integration**

    Messages you send here go directly to the Claude Code session running in your terminal.
    Claude Code will see your message and respond.
    """)

    # Initialize session state
    if "waiting_for_response" not in st.session_state:
        st.session_state.waiting_for_response = False
    if "pending_msg_id" not in st.session_state:
        st.session_state.pending_msg_id = None

    st.markdown("---")

    # Quick prompts
    st.subheader("Quick Prompts")
    cols = st.columns(4)

    quick_prompts = [
        ("📊 Market Status", "Give me an overview of my current market data and any notable patterns."),
        ("📈 NVDA Analysis", "Analyze NVDA - show me a price chart and key metrics."),
        ("🎯 Best Strategy", "Which trading strategy would perform best on my current data?"),
        ("🧠 ML Status", "What ML models do I have and what predictions are available?"),
    ]

    for i, (label, prompt) in enumerate(quick_prompts):
        with cols[i]:
            if st.button(label, use_container_width=True, disabled=st.session_state.waiting_for_response):
                msg_id = send_message(prompt)
                st.session_state.pending_msg_id = msg_id
                st.session_state.waiting_for_response = True
                st.rerun()

    st.markdown("---")

    # Chat history
    st.subheader("Conversation")

    chat_container = st.container()

    with chat_container:
        history = get_chat_history()
        for prompt, response, status, created_at in history:
            with st.chat_message("user"):
                st.markdown(prompt)
            if response:
                with st.chat_message("assistant"):
                    st.markdown(response)
            elif status == 'pending':
                with st.chat_message("assistant"):
                    st.info("⏳ Waiting for Claude Code to respond...")

    # Show waiting indicator
    if st.session_state.waiting_for_response and st.session_state.pending_msg_id:
        with st.spinner("Waiting for Claude Code response... (check your terminal)"):
            # Check every 2 seconds for up to 5 checks, then let user refresh
            response = get_response(st.session_state.pending_msg_id, timeout=10)
            if response:
                st.session_state.waiting_for_response = False
                st.session_state.pending_msg_id = None
                st.rerun()
            else:
                st.warning("Still waiting... Claude Code will respond when ready. Click 'Refresh' to check.")
                if st.button("🔄 Refresh"):
                    st.rerun()

    # Chat input
    st.markdown("---")
    prompt = st.chat_input(
        "Ask Claude Code anything about your trading data...",
        disabled=st.session_state.waiting_for_response
    )

    if prompt:
        msg_id = send_message(prompt)
        st.session_state.pending_msg_id = msg_id
        st.session_state.waiting_for_response = True
        st.rerun()

    # Sidebar controls
    with st.sidebar:
        st.markdown("### Chat Controls")

        if st.button("🔄 Refresh Chat", use_container_width=True):
            st.session_state.waiting_for_response = False
            st.session_state.pending_msg_id = None
            st.rerun()

        if st.button("🗑️ Clear History", use_container_width=True):
            from g2.ui.components.database import get_connection
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM ui_messages")
                    conn.commit()
            st.session_state.waiting_for_response = False
            st.session_state.pending_msg_id = None
            st.rerun()

        st.markdown("---")
        st.markdown("### How it Works")
        st.markdown("""
        1. You type a message here
        2. It's sent to Claude Code via the database
        3. Claude Code sees it and responds
        4. Response appears here

        **Note:** Claude Code must be running!
        """)
