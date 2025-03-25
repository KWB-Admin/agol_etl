import requests
import json
from yaml import load, Loader

if __name__ == "__main__":
    survey = ""
    etl_yaml = load(open("yaml/etl_variables.yaml", "r"), Loader)
    token = etl_yaml["token"]
    url = etl_yaml["surveys"][survey]["url"]

    layer_params = {
        "f": "pjson",
        "token": token,
    }

    layer_r = requests.get(url, layer_params)
    layer_json = layer_r.json()
    with open("datatest.json", "w") as file:
        json.dump(layer_json, file, indent=4)
