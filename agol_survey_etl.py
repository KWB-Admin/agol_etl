import polars, os, logging, requests, json
from kwb_loader import loader
from datetime import datetime
from yaml import load, Loader

logging.basicConfig(
    filename="log/agol_etl.log",
    encoding="utf-8",
    filemode="a",
    format="{asctime} - {levelname} - {message}",
    style="{",
    datefmt="%Y-%m-%d %H:%M",
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

user = os.getenv("kwb_dw_user")
host = os.getenv("kwb_dw_host")
password = os.getenv("kwb_dw_password")


def query_agol_data(token: str, survey_params: dict, date_ran: str):
    """
    This queryies survey layers in AGOL and dumps the returned results
    as a json.

    Args:
        token: str, API token as provided/generated by Esri
        survey_params: dict, dictionary of parameters used for specific survey query
        date_ran: str, date on which etl was ran
    """
    layer_url = survey_params["url"]

    layer_params = {
        "f": "pjson",
        "token": token,
    }
    try:
        layer_r = requests.get(layer_url, layer_params)
        if layer_r.status_code == 200:
            logger.info("Successfully %s queried data!" % (survey_params["name"]))
        else:
            logging.exception(
                "Bad request - check %s url and debug" % (survey_params["name"])
            )
    except requests.exceptions.RequestException as e:
        logger.error(f"API request failed: {e}")
    layer_json = layer_r.json()
    data_file_name = "raw_data/%s_%s.json" % (survey_params["name"], date_ran)
    with open(data_file_name, "w") as file:
        json.dump(layer_json, file, indent=4)


def transform_agol_data(survey_params, date_ran) -> str:
    """
    This transforms json data into parquets with correct data format.

    Args:
        survey_params: dict, dictionary of parameters used for specific survey query
        date_ran: str, date on which etl was ran
    Returns:
        proccessed_data_path: str, path to parquet file to be loaded to db
    """
    schema = build_schema(survey_params)

    with open("raw_data/%s_%s.json" % (survey_params["name"], date_ran), "r") as file:
        data = json.load(file)

    df = (
        polars.from_dicts(
            [feature["attributes"] for feature in data["features"]], schema=schema
        )
        .with_columns(polars.from_epoch("date_collected", time_unit="ms"))
        .cast({"date_collected": polars.Date})
    )
    proccessed_data_path = ("processed_data/%s_%s.parquet") % (
        survey_params["name"],
        date_ran,
    )
    df.write_parquet(proccessed_data_path)
    return proccessed_data_path


def build_schema(survey_params: dict) -> dict:
    """
    Builds schema based on survey_params input in etl_varibales.yaml
    Args:
        ssurvey_params: dict, dictionary of parameters used for specific survey query
    Returns:
        schema: dict, dictionary of colums with correct polars typing
    """
    schema: dict = survey_params["schema"]
    for key, value in schema.items():
        if value == "string":
            schema[key] = polars.String
        elif value == "int64":
            schema[key] = polars.Int64
        elif value == "float64":
            schema[key] = polars.Float64
        else:
            schema[key] = polars.String
    return schema


def load_agol_data(user, host, password, etl_yaml, survey_params, proccessed_data_path):
    """
    Loads agol survey data into db

    Args:
        user: str, username cred for db
        host: str, host on which db is hosted
        password: str, password cred for db
        etl_yaml: dict, dictionary of parameters used for etl
        survey_params: dict, dictionary of parameters used for specific survey query
        date_ran: str, date on which etl was ran
    """
    try:
        loader.load(
            credentials=(user, host, password),
            dbname=etl_yaml["db_name"],
            schema=etl_yaml["schema"],
            table_name=survey_params["table_name"],
            data_path=proccessed_data_path,
            prim_key=survey_params["prim_key"],
        )
        logging.info(
            "Successfully loaded data into %s.%s.%s \n"
            % (etl_yaml["db_name"], etl_yaml["schema"], survey_params["table_name"])
        )
    except:
        logger.exception("")


if __name__ == "__main__":
    date_ran = datetime.date(datetime.today())
    logger.info(
        "--------------- AGOL Survey ETL ran on %s ----------------" % (date_ran)
    )

    etl_yaml = load(open("yaml/etl_variables.yaml", "r"), Loader)
    token = etl_yaml["token"]
    for survey in etl_yaml["surveys"]:
        survey_params = etl_yaml["surveys"][survey]
        query_agol_data(token=token, survey_params=survey_params, date_ran=date_ran)

        proccessed_data_path = transform_agol_data(
            survey_params=survey_params, date_ran=date_ran
        )

        load_agol_data(
            user=user,
            host=host,
            password=password,
            etl_yaml=etl_yaml,
            survey_params=survey_params,
            proccessed_data_path=proccessed_data_path,
        )
