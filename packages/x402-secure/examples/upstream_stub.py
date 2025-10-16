# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
from fastapi import FastAPI

app = FastAPI(title="Upstream Facilitator Stub")


@app.post("/verify")
async def verify():
    return {"isValid": True, "payer": "0xabc"}


@app.post("/settle")
async def settle():
    return {"success": True, "payer": "0xabc", "transaction": "0xtx"}

