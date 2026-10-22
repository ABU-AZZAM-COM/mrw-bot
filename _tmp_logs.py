import os

from huggingface_hub import HfApi

api = HfApi(token=os.environ["HF_TOKEN"])
lines = list(api.fetch_space_logs(os.environ["HF_SPACE"]))
for line in lines[-28:]:
    print(line[:190])
