"""Runs in the isolated, pinned official-SDK environment against the local gateway."""

import argparse
import asyncio
import os

from openai import AsyncOpenAI


async def main(base_url: str, profile: str) -> None:
    async with AsyncOpenAI(
        base_url=base_url + "/v1",
        api_key=os.environ["DELTALLM_SDK_TEST_KEY"],
    ) as client:
        async with client.realtime.connect(
            model="voice",
            extra_query={"intent": "transcription"} if profile == "transcription" else {},
            max_retries=0,
            websocket_connection_options={"proxy": None},
        ) as socket:
            assert (await socket.recv()).type == "session.created"
            assert (await socket.recv()).type == "session.updated"
            for _ in range(2):
                if profile == "transcription":
                    await socket.input_audio_buffer.append(audio="AA==")
                    await socket.input_audio_buffer.commit()
                else:
                    await socket.conversation.item.create(
                        item={
                            "type": "message",
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": "Give a brief spoken greeting."}
                            ],
                        }
                    )
                    await socket.response.create(response={"output_modalities": ["audio"]})
                terminal = await asyncio.wait_for(socket.recv(), 5)
                assert terminal.type in {
                    "response.done",
                    "conversation.item.input_audio_transcription.completed",
                }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--profile", choices=["realtime", "transcription"], required=True)
    args = parser.parse_args()
    asyncio.run(main(args.base_url, args.profile))
