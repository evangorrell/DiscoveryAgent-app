================================================================================
ACS AGENT - AMERICAN COMMUNITY SURVEY DATA ASSISTANT
================================================================================

The ACS Agent provides access to the U.S. Census Bureau's American Community 
Survey (ACS), the largest annual survey of demographic, social, economic, and 
housing data in the United States.

================================================================================
AVAILABLE DATA
================================================================================

DATASETS
--------
- ACS 5-Year Estimates (default): Most reliable, available for ALL geographies
- ACS 1-Year Estimates: More current, but only for areas with 65,000+ population
- Subject Tables: Pre-computed summary tables
- Data Profiles: Key indicators for quick reference

YEARS AVAILABLE
---------------
- ACS 5-Year: 2009-2024
- ACS 1-Year: 2005-2024
- Most recent data: 2024

GEOGRAPHIC COVERAGE
-------------------
The ACS provides data at multiple geographic levels:

  Nation (US)
    └── Region (4 regions)
        └── Division (9 divisions)
            └── State (50 states + DC + territories)
                ├── County (~3,200 counties)
                │   ├── Census Tract (~85,000 tracts)
                │   │   └── Block Group (~240,000 block groups)
                │   └── County Subdivision
                ├── Place/City (~30,000 places)
                ├── Congressional District
                ├── School District
                └── ZIP Code Tabulation Area (ZCTA)

Note: 1-year estimates only available for areas with 65,000+ population.
      5-year estimates available for all geographies.

MAJOR DATA CATEGORIES
---------------------
The ACS covers these major topic areas:

  DEMOGRAPHICS
  - B01xxx: Age, Sex, Total Population
  - B02xxx: Race
  - B03xxx: Hispanic/Latino Origin
  - B05xxx: Citizenship Status, Place of Birth, Nativity

  SOCIAL CHARACTERISTICS
  - B06xxx: Place of Birth (detailed)
  - B07xxx: Geographic Mobility (migration)
  - B09xxx: Children's Characteristics
  - B11xxx: Household Type, Family Structure
  - B12xxx: Marital Status
  - B13xxx: Fertility

  ECONOMIC CHARACTERISTICS  
  - B15xxx: Educational Attainment
  - B17xxx: Poverty Status
  - B19xxx: Income (household, family, per capita)
  - B20xxx: Earnings
  - B21xxx: Veteran Status
  - B23xxx: Employment Status
  - B24xxx: Industry and Occupation

  HOUSING CHARACTERISTICS
  - B25xxx: Housing Units, Occupancy, Value, Rent, Costs
  - B26xxx: Group Quarters
  - B27xxx: Health Insurance Coverage
  - B28xxx: Computer and Internet Use

  TRANSPORTATION
  - B08xxx: Commuting, Travel Time, Means of Transportation

  LANGUAGE
  - B16xxx: Language Spoken at Home, English Proficiency

COMMONLY REQUESTED TABLES
-------------------------
Table ID    | Description                          | Variables
------------|--------------------------------------|----------
B01001      | Sex by Age                           | 49
B01003      | Total Population                     | 1
B02001      | Race                                 | 10
B03002      | Hispanic Origin by Race              | 21
B15003      | Educational Attainment               | 25
B17001      | Poverty Status                       | 59
B19013      | Median Household Income              | 1
B19001      | Household Income Distribution        | 17
B23025      | Employment Status                    | 7
B25001      | Housing Units                        | 1
B25003      | Tenure (Own vs Rent)                 | 3
B25077      | Median Home Value                    | 1
B25064      | Median Gross Rent                    | 1

================================================================================
AGENT CAPABILITIES (TOOLS)
================================================================================

The ACS Agent has the following tools:

1. search_tables
   - Find tables by topic/keyword
   - Example: "income", "education", "housing"

2. get_table_info
   - Get details about a specific table
   - Shows all variables and their descriptions

3. download_table
   - Download a complete table as CSV
   - Supports state, county, place geographies
   - Can download for all states ("*") or all counties

4. download_variables
   - Download specific variables (not full table)
   - More efficient for targeted queries

5. search_variables
   - Find specific variable codes by keyword
   - Useful for precise data points

6. get_quick_stats
   - Get common metrics for a state quickly
   - Returns: population, income, poverty rate, unemployment, education

7. compare_geographies
   - Compare statistics across multiple states
   - Good for comparative analysis

================================================================================
PROMPTING TIPS
================================================================================

EFFECTIVE PROMPTS
-----------------
✓ "What is the median household income in California?"
  → Uses get_quick_stats or downloads B19013

✓ "Download the educational attainment table for Texas"
  → Downloads B15003 for Texas

✓ "Compare poverty rates between Michigan, Ohio, and Indiana"
  → Uses compare_geographies tool

✓ "Find tables about housing costs"
  → Uses search_tables with "housing costs"

✓ "Get population data for all counties in Florida"
  → Downloads B01003 with state=FL, county=*

✓ "What percentage of people in Detroit have a bachelor's degree?"
  → Downloads B15003 for Detroit and calculates percentage

INCLUDE THESE DETAILS WHEN POSSIBLE
------------------------------------
1. GEOGRAPHY: Be specific about location
   - State: "Michigan", "MI", or FIPS code "26"
   - County: "Wayne County" (include "County")
   - City: "Detroit" or "Detroit city"
   - Multiple: "California and Texas"
   - All: "all states" or "all counties in Michigan"

2. YEAR: Specify if not current year
   - "...in 2020"
   - "...from 2015 to 2024"
   - Default is 2024 if not specified

3. SPECIFICITY: Help narrow down tables
   - "median household income" vs just "income"
   - "educational attainment for adults 25+" vs "education"
   - "housing costs" vs "housing"

AVOID
-----
✗ Vague locations: "What is the population?" (where?)
✗ Ambiguous cities: "Portland" (Oregon or Maine?)
✗ Non-ACS data: Stock prices, weather, sports statistics
✗ Future projections: ACS is historical/current data only
✗ Individual-level data: ACS only provides aggregated statistics

================================================================================
DATA LIMITATIONS
================================================================================

WHAT ACS DOES NOT HAVE
----------------------
- Real-time data (most recent is previous year)
- Individual-level records (privacy protected)
- Business/establishment data (see Census Business surveys)
- Vital statistics details (see CDC/NCHS)
- Crime statistics (see FBI UCR)
- Election results (see FEC)
- Environmental data (see EPA)

MARGIN OF ERROR
---------------
All ACS estimates have margins of error (MOE). Smaller geographies and 
smaller populations have larger MOEs. The agent can include or exclude 
MOE columns in downloads.

- Variables ending in "E" = Estimate
- Variables ending in "M" = Margin of Error

GEOGRAPHY NOTES
---------------
- ZCTAs are NOT the same as ZIP codes (approximations)
- Place boundaries may cross county lines
- Some areas have no data (population too small)
- Puerto Rico and territories have limited coverage

================================================================================
EXAMPLE QUERIES FOR DIRECTOR AGENT
================================================================================

DEMOGRAPHIC ANALYSIS
--------------------
"Get the age distribution for [STATE] to understand the population pyramid"
"What is the racial composition of [COUNTY]?"
"Download population by sex and age for all states"

ECONOMIC ANALYSIS
-----------------
"What is the median household income in [CITY]?"
"Compare poverty rates across [LIST OF STATES]"
"Download income distribution data for [STATE] counties"
"What percentage of [STATE] residents are below the poverty line?"

HOUSING ANALYSIS
----------------
"What is the median home value in [COUNTY]?"
"Compare homeownership rates between [STATE1] and [STATE2]"
"Get rent burden data for [CITY]"
"Download housing cost data for all counties in [STATE]"

EDUCATION ANALYSIS
------------------
"What is the educational attainment in [STATE]?"
"Compare bachelor's degree rates across [STATES]"
"What percentage of [CITY] residents have graduate degrees?"

LABOR ANALYSIS
--------------
"What is the unemployment rate in [STATE]?"
"Get commute time data for [COUNTY]"
"Download employment by industry for [STATE]"

TIME SERIES
-----------
"Get median income for [STATE] from 2015 to 2024"
"How has the poverty rate changed in [COUNTY] over the last 10 years?"

================================================================================
OUTPUT FORMAT
================================================================================

Downloads are saved as CSV files with:
- All requested variables
- Geographic identifiers (state, county, place FIPS codes)
- NAME column with human-readable geography name
- _year, _dataset, _table_id metadata columns
- Optional: Margin of Error columns (ending in M)

File naming: {TABLE_ID}_{GEOGRAPHY}_{YEAR}.csv
Example: B19013_Michigan_2024.csv

================================================================================


