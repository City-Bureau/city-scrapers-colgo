# City Scrapers - Columbia Gorge

Web scrapers for public meetings in the Columbia Gorge region (Oregon & Washington).

**Spider prefix**: `colgo`

## Overview

This repository covers agencies across multiple counties in the Columbia Gorge region:
- Hood River County, OR
- Wasco County, OR
- Klickitat County, WA
- Skamania County, WA

## Agencies (100+ total)

### Hood River Area
- Hood River City Council
- Hood River County Board of Commissioners
- Hood River County Planning Commission
- Hood River County Library District Board
- Hood River Parks and Rec District Board
- Port of Hood River Commission Board
- And more...

### Wasco County Area
- The Dalles City Council
- Wasco County Board of Commissioners
- Port of The Dalles
- North Wasco County School District Board
- And more...

### Klickitat County Area
- Klickitat County Board of County Commissioners
- Klickitat County Planning Commission
- Goldendale City Council
- White Salmon City Council
- Bingen City Council
- And more...

### Skamania County Area
- Skamania County Board of Commissioners
- Stevenson City Council
- North Bonneville City Council
- Port of Skamania County Board of Commissioners
- And more...

### Regional Bodies
- Columbia River Gorge Commission
- Columbia Gorge Bi-State Advisory Council
- Mid-Columbia Economic Development District
- Columbia Gorge Community College Board
- And more...

## Setup

```bash
pipenv install --dev
```

## Running Scrapers

```bash
# Run a specific scraper
pipenv run scrapy crawl colgo_hood_river_city_council

# Run all scrapers
pipenv run scrapy list | xargs -I {} pipenv run scrapy crawl {}
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.
