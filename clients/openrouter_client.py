import httpx
import base64
from pathlib import Path
from typing import Optional


class OpenRouterClient:
    def __init__(self, api_key: str, base_url: str = "https://openrouter.ai/api/v1"):
        self.api_key = api_key
        self.base_url = base_url
        self.client = httpx.Client(
            headers={
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://homeimpruv.com",
                "X-Title": "HomeImpruv Photo Generator",
            },
            timeout=120.0,
        )

    def generate(
        self,
        prompt: str,
        reference_image: Optional[Path] = None,
        width: int = 1024,
        height: int = 768,
        steps: int = 30,
        negative_prompt: str = "text, watermark, blurry, low quality",
    ) -> bytes:
        payload = {
            "model": "stability/stable-diffusion-xl-base-1.0",
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "width": width,
            "height": height,
            "steps": steps,
            "cfg_scale": 7.0,
        }

        if reference_image:
            with open(reference_image, "rb") as f:
                payload["init_image"] = base64.b64encode(f.read()).decode()

        response = self.client.post(f"{self.base_url}/images/generations", json=payload)
        response.raise_for_status()
        result = response.json()

        image_data = result["data"][0].get("b64_json") or result["data"][0].get("url")
        if result["data"][0].get("b64_json"):
            return base64.b64decode(image_data)

        img_response = httpx.get(image_data)
        img_response.raise_for_status()
        return img_response.content

    def chat_vision(
        self,
        prompt: str,
        image_b64: str,
        model: str = "openai/gpt-4o-mini",
        max_tokens: int = 512,
    ) -> str:
        """Send image + prompt to a vision model, return text response."""
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "max_tokens": max_tokens,
        }
        response = self.client.post(f"{self.base_url}/chat/completions", json=payload)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    def enhance_vision(
        self,
        prompt: str,
        image_b64: str,
        model: str = "google/gemini-3.1-flash-image-preview",
        max_tokens: int = 4096,
    ) -> dict:
        """
        Send image to an image generation/editing model for enhancement.
        Automatically selects payload format based on model provider:
          - google/gemini-*  → modalities: ["image", "text"]
          - sourceful/*      → no modalities field (native routing)
          - others           → no modalities field
        Returns raw response JSON (caller handles image extraction).
        """
        payload: dict = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "max_tokens": max_tokens,
        }
        # Gemini image models require explicit modalities declaration
        if model.startswith("google/"):
            payload["modalities"] = ["image", "text"]

        response = self.client.post(f"{self.base_url}/chat/completions", json=payload)
        response.raise_for_status()
        return response.json()

    def close(self):
        self.client.close()
