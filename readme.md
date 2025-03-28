# Description

This is an ETL pipeline for processing Esri survey data and loading it into the KWB data warehouse.

Once data is collected via Survey123, this script queries that data via Rest API and stores it locally as a json file. Then the script loads it into the KWB warehouse.

# Requirements

This software relies on these dependies:

1. `psychopg2`
2. `polars`
3. `numpy`
4. `PyYAML`
5. `requests`

Please see the requirements.txt file for specific versions.

# Operations

The transformations on these are very lite since the survey's enfore data type on their end. The main transformations are casting to correct types, casting unix timecodes to more readable date types.