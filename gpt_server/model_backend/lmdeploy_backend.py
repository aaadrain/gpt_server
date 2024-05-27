import os
from lmdeploy import (
    GenerationConfig,
    TurbomindEngineConfig,
    PytorchEngineConfig,
)
from typing import Any, Dict, AsyncGenerator
from lmdeploy.serve.async_engine import AsyncEngine
from loguru import logger
from gpt_server.model_backend.base import ModelBackend

backend_map = {
    "lmdeploy-pytorch": "pytorch",  # pytorch后端
    "lmdeploy-turbomind": "turbomind",  # turbomind后端
}


class LMDeployBackend(ModelBackend):
    def __init__(self, model_path) -> None:
        backend = backend_map[os.getenv("backend")]
        logger.info(f"后端 {backend}")
        if backend == "pytorch":
            backend_config = PytorchEngineConfig(
                model_name="", tp=int(os.getenv("num_gpus", "1")), thread_safe=False
            )
        if backend == "turbomind":
            backend_config = TurbomindEngineConfig(
                model_name="", tp=int(os.getenv("num_gpus", "1")), thread_safe=True
            )
        self.async_engine = AsyncEngine(
            model_path=model_path,
            backend=backend,
            backend_config=backend_config,
        )

    async def stream_chat(self, params: Dict[str, Any]) -> AsyncGenerator:
        prompt = params.pop("prompt")
        messages = params["messages"]
        request_id = params.get("request_id", "0")
        temperature = float(params.get("temperature", 0.8))
        top_p = float(params.get("top_p", 0.8))
        top_k = params.get("top_k", 1)

        max_new_tokens = min(int(params.get("max_new_tokens", 1024 * 8)), 1024 * 4)
        stop_str = params.get("stop", None)
        stop_token_ids = params.get("stop_words_ids", None) or []
        presence_penalty = float(params.get("presence_penalty", 0.0))
        frequency_penalty = float(params.get("frequency_penalty", 0.0))
        request = params.get("request", None)
        # Handle stop_str
        stop = set()
        if isinstance(stop_str, str) and stop_str != "":
            stop.add(stop_str)
        elif isinstance(stop_str, list) and stop_str != []:
            stop.update(stop_str)
        input_ids = params.get("input_ids")
        prompt_token_ids = input_ids.tolist()[0]
        # make sampling params in vllm
        top_p = max(top_p, 1e-5)
        if temperature <= 1e-5:
            top_p = 1.0
            temperature = 0.01
        gen_config = GenerationConfig(
            top_p=top_p,
            temperature=temperature,
            max_new_tokens=max_new_tokens,  # 存在问题
            top_k=1 if top_k == -1 else top_k,
            stop_words=list(stop),
            skip_special_tokens=True,
        )
        logger.info(f"request_id {int(request_id)}")
        results_generator = self.async_engine.generate(
            messages=messages, session_id=int(request_id), gen_config=gen_config
        )
        text_outputs = ""
        async for request_output in results_generator:
            if await request.is_disconnected():
                # Abort the request if the client disconnects.
                await self.async_engine.stop_session(session_id=request_id)
            text_outputs += request_output.response

            usage = {
                "prompt_tokens": request_output.input_token_len,
                "completion_tokens": request_output.generate_token_len,
                "total_tokens": request_output.input_token_len
                + request_output.generate_token_len,
            }
            yield text_outputs, usage
        logger.info(text_outputs)
        logger.info(usage)
