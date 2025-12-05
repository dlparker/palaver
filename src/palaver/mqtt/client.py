"""
palaver/mqtt/client.py
Async MQTT client wrapper for local broker publishing

Provides a simple interface for publishing events to a local MQTT broker
with QoS 1 (at least once delivery) and no message retention.
"""

import asyncio
from typing import Optional

try:
    import aiomqtt
    AsyncMQTTClient = aiomqtt.Client
except ImportError:
    AsyncMQTTClient = None  # Handle missing dependency gracefully


class MQTTPublisher:
    """
    Async MQTT client wrapper for local broker publishing.

    Simplified client for publishing to a local MQTT broker without
    TLS/authentication. Uses QoS 1 for at-least-once delivery.
    """

    def __init__(self, broker: str = "localhost", port: int = 1883, qos: int = 1):
        """
        Initialize MQTT publisher.

        Args:
            broker: MQTT broker hostname (default: localhost)
            port: MQTT broker port (default: 1883)
            qos: Quality of Service level (default: 1 = at least once)

        Raises:
            ImportError: If asyncio-mqtt is not installed
        """
        if AsyncMQTTClient is None:
            raise ImportError(
                "aiomqtt is required for MQTT support. "
                "Install it with: uv pip install aiomqtt"
            )

        self.broker = broker
        self.port = port
        self.qos = qos
        self.client: Optional[AsyncMQTTClient] = None
        self._context = None

    async def connect(self):
        """
        Connect to MQTT broker.

        Establishes connection to the broker. Must be called before publishing.
        """
        self.client = AsyncMQTTClient(
            hostname=self.broker,
            port=self.port
            # No TLS/auth for local broker
        )
        # Enter the client context
        self._context = await self.client.__aenter__()

    async def disconnect(self):
        """
        Disconnect from MQTT broker and cleanup resources.

        Safe to call even if not connected.
        """
        if self.client and self._context:
            await self.client.__aexit__(None, None, None)
            self.client = None
            self._context = None

    async def publish(self, topic: str, payload: str, retain: bool = False):
        """
        Publish message to MQTT broker.

        Args:
            topic: MQTT topic to publish to
            payload: Message payload (string, typically JSON)
            retain: Whether to retain message on broker (default: False)

        Raises:
            RuntimeError: If not connected to broker
        """
        if not self.client:
            raise RuntimeError("Not connected to MQTT broker. Call connect() first.")

        await self.client.publish(topic, payload, qos=self.qos, retain=retain)

    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.disconnect()
