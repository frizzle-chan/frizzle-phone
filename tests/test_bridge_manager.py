"""Unit tests for BridgeManager lifecycle: start(), stop(), shutdown()."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from frizzle_phone.bridge_manager import BridgeHandle, BridgeManager

# ---------------------------------------------------------------------------
# BridgeHandle unit tests
# ---------------------------------------------------------------------------


def test_bridge_handle_stop_sets_event():
    handle = BridgeHandle(
        stop_event=asyncio.Event(),
        send_task=MagicMock(),
        rtp_transport=MagicMock(),
        voice_client=MagicMock(),
    )
    handle.stop()
    assert handle._stop_event.is_set()


def test_bridge_handle_stop_cancels_task():
    task = MagicMock()
    handle = BridgeHandle(
        stop_event=asyncio.Event(),
        send_task=task,
        rtp_transport=MagicMock(),
        voice_client=MagicMock(),
    )
    handle.stop()
    task.cancel.assert_called_once()


def test_bridge_handle_stop_closes_transport():
    transport = MagicMock()
    handle = BridgeHandle(
        stop_event=asyncio.Event(),
        send_task=MagicMock(),
        rtp_transport=transport,
        voice_client=MagicMock(),
    )
    handle.stop()
    transport.close.assert_called_once()


def test_bridge_handle_stop_calls_voice_client_stop():
    vc = MagicMock()
    handle = BridgeHandle(
        stop_event=asyncio.Event(),
        send_task=MagicMock(),
        rtp_transport=MagicMock(),
        voice_client=vc,
    )
    handle.stop()
    vc.stop.assert_called_once()


def test_bridge_handle_stop_calls_stop_listening():
    vc = MagicMock()
    handle = BridgeHandle(
        stop_event=asyncio.Event(),
        send_task=MagicMock(),
        rtp_transport=MagicMock(),
        voice_client=vc,
    )
    handle.stop()
    vc.stop_listening.assert_called_once()


def test_bridge_handle_stop_idempotent():
    """Calling stop() twice should not raise."""
    handle = BridgeHandle(
        stop_event=asyncio.Event(),
        send_task=MagicMock(),
        rtp_transport=MagicMock(),
        voice_client=MagicMock(),
    )
    handle.stop()
    handle.stop()  # no error


# ---------------------------------------------------------------------------
# BridgeManager unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_returns_handle():
    """start() wires up components and returns a BridgeHandle."""
    mgr = BridgeManager()

    vc = MagicMock()
    transport_mock = MagicMock()

    with patch.object(
        asyncio.get_running_loop(),
        "create_datagram_endpoint",
        new_callable=AsyncMock,
        return_value=(transport_mock, MagicMock()),
    ):
        handle = await mgr.start(vc, 20000, ("10.0.0.1", 30000))

    assert isinstance(handle, BridgeHandle)
    vc.play.assert_called_once()
    vc.start_listening.assert_called_once()


@pytest.mark.asyncio
async def test_start_registers_task_in_bridge_tasks():
    """start() adds the send task to _bridge_tasks."""
    mgr = BridgeManager()

    vc = MagicMock()
    transport_mock = MagicMock()

    with patch.object(
        asyncio.get_running_loop(),
        "create_datagram_endpoint",
        new_callable=AsyncMock,
        return_value=(transport_mock, MagicMock()),
    ):
        handle = await mgr.start(vc, 20000, ("10.0.0.1", 30000))

    assert handle._send_task in mgr._bridge_tasks


@pytest.mark.asyncio
async def test_shutdown_cancels_all_tasks():
    """shutdown() cancels all tracked bridge tasks and clears the set."""
    mgr = BridgeManager()

    vc = MagicMock()
    transport_mock = MagicMock()

    with patch.object(
        asyncio.get_running_loop(),
        "create_datagram_endpoint",
        new_callable=AsyncMock,
        return_value=(transport_mock, MagicMock()),
    ):
        handle1 = await mgr.start(vc, 20000, ("10.0.0.1", 30000))
        handle2 = await mgr.start(vc, 20001, ("10.0.0.1", 30001))

    assert len(mgr._bridge_tasks) == 2

    mgr.shutdown()

    assert len(mgr._bridge_tasks) == 0
    # Tasks were cancel()'d — they're in "cancelling" state until the
    # event loop processes them.
    assert handle1._send_task.cancelling() > 0
    assert handle2._send_task.cancelling() > 0


@pytest.mark.asyncio
async def test_shutdown_empty_is_noop():
    """shutdown() on a fresh manager does nothing."""
    mgr = BridgeManager()
    mgr.shutdown()  # no error
    assert len(mgr._bridge_tasks) == 0
