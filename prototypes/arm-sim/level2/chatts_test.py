"""Level 2 side test: can ChatTS-14B tell a frozen ultrasonic trace from a healthy one?

Runs on a RunPod GPU (A100 40 GB). The healthy trace is Olir's real pod sample
(firmware/ultrasonic-sample.json: 300 readings at 10 Hz, echo time 285-286 us, target ~49 mm).
The planted copies: frozen (no jitter at all), dropouts (no echo = 0), spikes. Olir's rules miss the
frozen copy; only the 1 us jitter tells it apart.
"""
import json
import random

import numpy as np
import torch
from transformers import AutoModelForCausalLM, AutoProcessor, AutoTokenizer

HEALTHY = [286, 285, 286, 286, 285, 286, 286, 286, 286, 286, 286, 286, 286, 285, 285, 285, 285, 286, 286, 286, 285, 286, 286, 286, 285, 286, 286, 285, 286, 286, 286, 285, 286, 286, 286, 286, 285, 286, 285, 285, 286, 286, 286, 286, 286, 286, 286, 286, 286, 285, 286, 286, 286, 286, 286, 286, 286, 286, 285, 285, 286, 286, 286, 286, 286, 286, 286, 286, 286, 285, 286, 285, 286, 286, 286, 285, 286, 286, 286, 286, 286, 286, 285, 286, 286, 286, 285, 286, 285, 285, 285, 285, 286, 285, 286, 286, 286, 286, 285, 286, 286, 286, 286, 286, 286, 285, 285, 285, 286, 286, 286, 286, 286, 285, 286, 286, 286, 286, 286, 286, 286, 286, 286, 286, 286, 286, 286, 285, 286, 286, 286, 286, 286, 286, 286, 286, 286, 286, 286, 286, 286, 286, 286, 286, 286, 286, 285, 286, 286, 286, 286, 286, 285, 286, 285, 286, 286, 286, 285, 286, 286, 286, 286, 286, 286, 286, 286, 286, 285, 286, 286, 286, 286, 286, 286, 285, 285, 286, 286, 286, 286, 286, 286, 285, 286, 286, 286, 286, 285, 286, 286, 286, 286, 285, 285, 286, 286, 286, 286, 285, 286, 286, 286, 286, 285, 286, 286, 285, 286, 285, 286, 286, 285, 286, 286, 286, 286, 286, 286, 286, 285, 286, 286, 285, 286, 286, 285, 286, 286, 286, 285, 286, 286, 286, 286, 286, 286, 285, 286, 285, 286, 286, 286, 286, 286, 286, 286, 286, 286, 286, 286, 286, 286, 285, 285, 286, 286, 286, 286, 286, 286, 286, 286, 286, 286, 286, 286, 286, 285, 286, 286, 286, 285, 286, 286, 286, 286, 286, 286, 286, 286, 285, 286, 286, 285, 286, 286, 286, 286, 286, 286, 286, 285, 286, 286, 286, 286, 286, 286, 286]

def variants():
    rng = random.Random(7)
    frozen = [HEALTHY[0]] * len(HEALTHY)
    drop = list(HEALTHY)
    for i in rng.sample(range(len(drop)), 24):
        drop[i] = 0
    spikes = list(HEALTHY)
    for i in rng.sample(range(len(spikes)), 6):
        spikes[i] += rng.choice([180, 240, 320])
    return {"healthy": HEALTHY, "frozen": frozen, "dropouts": drop, "spikes": spikes}

CONTEXT = ("An HC-SR04 ultrasonic distance sensor reports the echo time in microseconds, sampled at 10 Hz for 30 s. "
           "The target is a still object about 49 mm away.")

def chat(user):
    return ("<|im_start|>system\nYou are a helpful assistant.<|im_end|><|im_start|>user\n"
            + user + "<|im_end|><|im_start|>assistant\n")

def main():
    hf = "bytedance-research/ChatTS-14B"
    model = AutoModelForCausalLM.from_pretrained(hf, trust_remote_code=True, device_map="auto", torch_dtype=torch.float16)
    tok = AutoTokenizer.from_pretrained(hf, trust_remote_code=True)
    proc = AutoProcessor.from_pretrained(hf, trust_remote_code=True, tokenizer=tok)

    def ask(user, series):
        inputs = proc(text=[chat(user)], timeseries=[np.array(s, dtype=float) for s in series], padding=True, return_tensors="pt")
        inputs = {k: v.to(model.device) for k, v in inputs.items()}
        out = model.generate(**inputs, max_new_tokens=220, do_sample=False)
        return tok.decode(out[0][len(inputs["input_ids"][0]):], skip_special_tokens=True).strip()

    v = variants()
    results = []
    for name, s in v.items():
        q = (f"{CONTEXT} Here are {len(s)} readings: <ts><ts/>. Is the sensor working normally? "
             "If not, what is wrong? Answer in two sentences.")
        results.append({"test": "single", "variant": name, "answer": ask(q, [s])})
        print(json.dumps(results[-1]), flush=True)
    q = (f"{CONTEXT} Two sensors watched the same object. Sensor A: <ts><ts/>. Sensor B: <ts><ts/>. "
         "One of them may be faulty. Which one, and why? Answer in two sentences.")
    results.append({"test": "pair", "variant": "A=healthy, B=frozen", "answer": ask(q, [v["healthy"], v["frozen"]])})
    print(json.dumps(results[-1]), flush=True)
    with open("/workspace/chatts_results.jsonl", "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")

if __name__ == "__main__":
    main()
