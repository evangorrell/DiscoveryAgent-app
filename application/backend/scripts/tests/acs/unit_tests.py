"""
Unit tests for the acs_access package.

Run with: pytest unit_tests.py -v
Or: python -m pytest unit_tests.py -v

Requirements:
    pip install pytest pytest-asyncio aioresponses
"""

import os
import re
import json
import tempfile
import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
import aiohttp
from aioresponses import aioresponses

# Import package components
from acs_access.config import (
    ACSConfig,
    ACSDataset,
    GeographyLevel,
    STATE_FIPS,
    STATE_TO_FIPS,
    STATE_ABBREV_TO_FIPS,
    DATASET_YEARS,
    CENSUS_API_BASE,
    RATE_LIMIT_ANONYMOUS,
    RATE_LIMIT_WITH_KEY,
)
from acs_access.models import (
    GeographySpec,
    TableInfo,
    VariableInfo,
    DownloadResult,
    BatchDownloadResult,
)
from acs_access.cache import MetadataCache, GeographyCache
from acs_access.geography import GeographyResolver
from acs_access.client import ACSClient


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def temp_cache_dir():
    """Create a temporary directory for cache testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def config_no_key(temp_cache_dir):
    """Config without API key."""
    with patch.dict(os.environ, {"CENSUS_API_KEY": "", "ACS_CACHE_PATH": str(temp_cache_dir)}, clear=False):
        # Clear any existing key
        if "CENSUS_API_KEY" in os.environ:
            del os.environ["CENSUS_API_KEY"]
        return ACSConfig(cache_path=temp_cache_dir)


@pytest.fixture
def config_with_key(temp_cache_dir):
    """Config with API key."""
    return ACSConfig(api_key="test_api_key_12345", cache_path=temp_cache_dir)


@pytest.fixture
def metadata_cache(temp_cache_dir):
    """Create a metadata cache instance."""
    return MetadataCache(temp_cache_dir, ttl_days=30)


@pytest.fixture
def sample_table_info():
    """Sample TableInfo for testing."""
    return TableInfo(
        table_id="B01001",
        title="SEX BY AGE",
        universe="Total population",
        variables=[
            VariableInfo(name="B01001_001E", label="Estimate!!Total:", concept="SEX BY AGE"),
            VariableInfo(name="B01001_001M", label="Margin of Error!!Total:", concept="SEX BY AGE"),
            VariableInfo(name="B01001_002E", label="Estimate!!Total:!!Male:", concept="SEX BY AGE"),
        ],
        dataset="acs/acs5",
        year=2022
    )


@pytest.fixture
def sample_geography_spec():
    """Sample GeographySpec for testing."""
    return GeographySpec(
        level=GeographyLevel.COUNTY,
        fips_code="163",
        parent_fips={"state": "26"},
        name="Wayne County, Michigan"
    )


# =============================================================================
# CONFIG TESTS
# =============================================================================

class TestACSConfig:
    """Tests for ACSConfig class."""
    
    def test_default_config_no_api_key(self, temp_cache_dir):
        """Test config without API key uses anonymous rate limit."""
        with patch.dict(os.environ, {}, clear=True):
            config = ACSConfig(cache_path=temp_cache_dir)
            assert config.api_key is None
            assert config.rate_limit == RATE_LIMIT_ANONYMOUS
    
    def test_config_with_api_key(self, temp_cache_dir):
        """Test config with API key uses higher rate limit."""
        config = ACSConfig(api_key="test_key", cache_path=temp_cache_dir)
        assert config.api_key == "test_key"
        assert config.rate_limit == RATE_LIMIT_WITH_KEY
    
    def test_config_from_env(self, temp_cache_dir):
        """Test config loads from environment variables."""
        with patch.dict(os.environ, {
            "CENSUS_API_KEY": "env_api_key",
            "ACS_CACHE_PATH": str(temp_cache_dir)
        }):
            config = ACSConfig()
            assert config.api_key == "env_api_key"
    
    def test_cache_directory_created(self, temp_cache_dir):
        """Test cache directory is created on init."""
        cache_path = temp_cache_dir / "new_cache_dir"
        config = ACSConfig(cache_path=cache_path)
        assert cache_path.exists()
    
    def test_with_api_key_method(self, config_no_key):
        """Test with_api_key returns new config."""
        new_config = config_no_key.with_api_key("new_key")
        assert new_config.api_key == "new_key"
        assert new_config.rate_limit == RATE_LIMIT_WITH_KEY
        # Original unchanged
        assert config_no_key.api_key is None


class TestACSDataset:
    """Tests for ACSDataset enum."""
    
    def test_dataset_values(self):
        """Test dataset enum values are correct."""
        assert ACSDataset.ACS1.value == "acs/acs1"
        assert ACSDataset.ACS5.value == "acs/acs5"
        assert ACSDataset.ACS5_SUBJECT.value == "acs/acs5/subject"
        assert ACSDataset.ACS5_PROFILE.value == "acs/acs5/profile"
    
    def test_dataset_years(self):
        """Test year ranges are defined for all datasets."""
        for dataset in ACSDataset:
            assert dataset in DATASET_YEARS
            years = DATASET_YEARS[dataset]
            assert len(years) > 0


class TestGeographyLevel:
    """Tests for GeographyLevel enum."""
    
    def test_geography_levels(self):
        """Test geography level values."""
        assert GeographyLevel.STATE.value == "state"
        assert GeographyLevel.COUNTY.value == "county"
        assert GeographyLevel.PLACE.value == "place"
        assert GeographyLevel.TRACT.value == "tract"
        assert GeographyLevel.BLOCK_GROUP.value == "block group"
        assert GeographyLevel.ZCTA.value == "zip code tabulation area"


class TestStateFIPS:
    """Tests for state FIPS code mappings."""
    
    def test_all_50_states_plus_dc(self):
        """Test all 50 states + DC are included."""
        # 50 states + DC = 51, plus 5 territories = 56
        assert len(STATE_FIPS) == 56
    
    def test_michigan_fips(self):
        """Test Michigan FIPS code."""
        assert STATE_FIPS["26"] == "Michigan"
        assert STATE_TO_FIPS["michigan"] == "26"
        assert STATE_ABBREV_TO_FIPS["mi"] == "26"
    
    def test_california_fips(self):
        """Test California FIPS code."""
        assert STATE_FIPS["06"] == "California"
        assert STATE_TO_FIPS["california"] == "06"
        assert STATE_ABBREV_TO_FIPS["ca"] == "06"
    
    def test_dc_fips(self):
        """Test DC FIPS code."""
        assert STATE_FIPS["11"] == "District of Columbia"
        assert STATE_ABBREV_TO_FIPS["dc"] == "11"


# =============================================================================
# MODEL TESTS
# =============================================================================

class TestGeographySpec:
    """Tests for GeographySpec dataclass."""
    
    def test_state_to_api_params(self):
        """Test converting state spec to API params."""
        spec = GeographySpec(
            level=GeographyLevel.STATE,
            fips_code="26",
            name="Michigan"
        )
        for_param, in_param = spec.to_api_params()
        assert for_param == "state:26"
        assert in_param is None
    
    def test_county_to_api_params(self):
        """Test converting county spec to API params."""
        spec = GeographySpec(
            level=GeographyLevel.COUNTY,
            fips_code="163",
            parent_fips={"state": "26"},
            name="Wayne County, Michigan"
        )
        for_param, in_param = spec.to_api_params()
        assert for_param == "county:163"
        assert in_param == "state:26"
    
    def test_tract_to_api_params(self):
        """Test converting tract spec to API params."""
        spec = GeographySpec(
            level=GeographyLevel.TRACT,
            fips_code="123456",
            parent_fips={"state": "26", "county": "163"},
        )
        for_param, in_param = spec.to_api_params()
        assert for_param == "tract:123456"
        assert "state:26" in in_param
        assert "county:163" in in_param
    
    def test_wildcard_state(self):
        """Test wildcard for all states."""
        spec = GeographySpec(
            level=GeographyLevel.STATE,
            fips_code="*",
            name="All States"
        )
        for_param, in_param = spec.to_api_params()
        assert for_param == "state:*"
    
    def test_str_representation(self):
        """Test string representation."""
        spec = GeographySpec(
            level=GeographyLevel.STATE,
            fips_code="26",
            name="Michigan"
        )
        assert str(spec) == "Michigan"


class TestTableInfo:
    """Tests for TableInfo dataclass."""
    
    def test_variable_count(self, sample_table_info):
        """Test variable count property."""
        assert sample_table_info.variable_count == 3
    
    def test_estimate_variables(self, sample_table_info):
        """Test filtering estimate variables."""
        estimates = sample_table_info.estimate_variables
        assert len(estimates) == 2
        assert all(v.name.endswith("E") for v in estimates)
    
    def test_moe_variables(self, sample_table_info):
        """Test filtering MOE variables."""
        moes = sample_table_info.moe_variables
        assert len(moes) == 1
        assert moes[0].name == "B01001_001M"
    
    def test_str_representation(self, sample_table_info):
        """Test string representation."""
        assert str(sample_table_info) == "B01001: SEX BY AGE"


class TestVariableInfo:
    """Tests for VariableInfo dataclass."""
    
    def test_is_estimate(self):
        """Test is_estimate property."""
        var_e = VariableInfo(name="B01001_001E", label="Total")
        var_m = VariableInfo(name="B01001_001M", label="MOE")
        
        assert var_e.is_estimate is True
        assert var_m.is_estimate is False
    
    def test_is_margin_of_error(self):
        """Test is_margin_of_error property."""
        var_e = VariableInfo(name="B01001_001E", label="Total")
        var_m = VariableInfo(name="B01001_001M", label="MOE")
        
        assert var_e.is_margin_of_error is False
        assert var_m.is_margin_of_error is True
    
    def test_is_percent(self):
        """Test is_percent property."""
        var_e = VariableInfo(name="B01001_001E", label="Total")
        var_pe = VariableInfo(name="S0101_C01_001PE", label="Percent")
        
        assert var_e.is_percent is False
        assert var_pe.is_percent is True
    
    def test_table_id(self):
        """Test table_id extraction."""
        var = VariableInfo(name="B01001_001E", label="Total")
        assert var.table_id == "B01001"


class TestDownloadResult:
    """Tests for DownloadResult dataclass."""
    
    def test_success_result(self, temp_cache_dir):
        """Test successful download result."""
        result = DownloadResult(
            success=True,
            file_path=temp_cache_dir / "test.csv",
            rows=100,
            columns=50
        )
        assert result.success is True
        assert bool(result) is True
        assert "Downloaded" in str(result)
    
    def test_failed_result(self):
        """Test failed download result."""
        result = DownloadResult(
            success=False,
            error="API error"
        )
        assert result.success is False
        assert bool(result) is False
        assert "Failed" in str(result)
    
    def test_to_dict(self, temp_cache_dir):
        """Test serialization to dict."""
        result = DownloadResult(
            success=True,
            file_path=temp_cache_dir / "test.csv",
            rows=100,
            columns=50,
            metadata={"table_id": "B01001"}
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["rows"] == 100
        assert d["metadata"]["table_id"] == "B01001"


class TestBatchDownloadResult:
    """Tests for BatchDownloadResult dataclass."""
    
    def test_from_results(self, temp_cache_dir):
        """Test creating batch result from individual results."""
        results = [
            DownloadResult(success=True, file_path=temp_cache_dir / "a.csv"),
            DownloadResult(success=True, file_path=temp_cache_dir / "b.csv"),
            DownloadResult(success=False, error="error"),
        ]
        
        batch = BatchDownloadResult.from_results(results)
        assert batch.total == 3
        assert batch.successful == 2
        assert batch.failed == 1
        assert bool(batch) is False  # Not all succeeded
    
    def test_all_successful(self, temp_cache_dir):
        """Test batch with all successful downloads."""
        results = [
            DownloadResult(success=True, file_path=temp_cache_dir / "a.csv"),
            DownloadResult(success=True, file_path=temp_cache_dir / "b.csv"),
        ]
        
        batch = BatchDownloadResult.from_results(results)
        assert bool(batch) is True


# =============================================================================
# CACHE TESTS
# =============================================================================

class TestMetadataCache:
    """Tests for MetadataCache class."""
    
    def test_set_and_get(self, metadata_cache):
        """Test storing and retrieving values."""
        test_data = {"key": "value", "number": 42}
        metadata_cache.set(test_data, "test", "key", "123")
        
        retrieved = metadata_cache.get("test", "key", "123")
        assert retrieved == test_data
    
    def test_get_nonexistent(self, metadata_cache):
        """Test getting nonexistent key returns None."""
        result = metadata_cache.get("nonexistent", "key")
        assert result is None
    
    def test_delete(self, metadata_cache):
        """Test deleting cached values."""
        metadata_cache.set("value", "to", "delete")
        assert metadata_cache.get("to", "delete") == "value"
        
        metadata_cache.delete("to", "delete")
        assert metadata_cache.get("to", "delete") is None
    
    def test_clear(self, metadata_cache):
        """Test clearing all cached values."""
        metadata_cache.set("value1", "key1")
        metadata_cache.set("value2", "key2")
        
        metadata_cache.clear()
        
        assert metadata_cache.get("key1") is None
        assert metadata_cache.get("key2") is None
    
    def test_stats(self, metadata_cache):
        """Test cache statistics."""
        metadata_cache.set("value", "key")
        stats = metadata_cache.stats()
        
        assert "size_bytes" in stats
        assert "item_count" in stats
        assert stats["item_count"] >= 1


class TestGeographyCache:
    """Tests for GeographyCache class."""
    
    def test_county_cache(self, metadata_cache):
        """Test county caching."""
        geo_cache = GeographyCache(metadata_cache)
        
        counties = {"Wayne County": "163", "Oakland County": "125"}
        geo_cache.set_counties("26", 2022, counties)
        
        retrieved = geo_cache.get_counties("26", 2022)
        assert retrieved == counties
    
    def test_place_cache(self, metadata_cache):
        """Test place caching."""
        geo_cache = GeographyCache(metadata_cache)
        
        places = {"Detroit city": "22000", "Ann Arbor city": "03000"}
        geo_cache.set_places("26", 2022, places)
        
        retrieved = geo_cache.get_places("26", 2022)
        assert retrieved == places
    
    def test_clear_memory_cache(self, metadata_cache):
        """Test clearing in-memory cache."""
        geo_cache = GeographyCache(metadata_cache)
        
        geo_cache.set_counties("26", 2022, {"Wayne County": "163"})
        geo_cache.clear_memory_cache()
        
        # Should still be in disk cache
        retrieved = geo_cache.get_counties("26", 2022)
        assert retrieved is not None


# =============================================================================
# GEOGRAPHY RESOLVER TESTS
# =============================================================================

class TestGeographyResolver:
    """Tests for GeographyResolver class."""
    
    def test_resolve_state_by_name(self, metadata_cache):
        """Test resolving state by full name."""
        resolver = GeographyResolver(metadata_cache)
        fips = resolver._resolve_state("Michigan")
        assert fips == "26"
    
    def test_resolve_state_by_abbrev(self, metadata_cache):
        """Test resolving state by abbreviation."""
        resolver = GeographyResolver(metadata_cache)
        fips = resolver._resolve_state("MI")
        assert fips == "26"
    
    def test_resolve_state_by_fips(self, metadata_cache):
        """Test resolving state by FIPS code."""
        resolver = GeographyResolver(metadata_cache)
        fips = resolver._resolve_state("26")
        assert fips == "26"
    
    def test_resolve_state_case_insensitive(self, metadata_cache):
        """Test state resolution is case insensitive."""
        resolver = GeographyResolver(metadata_cache)
        assert resolver._resolve_state("MICHIGAN") == "26"
        assert resolver._resolve_state("michigan") == "26"
        assert resolver._resolve_state("mi") == "26"
        assert resolver._resolve_state("MI") == "26"
    
    def test_resolve_state_invalid(self, metadata_cache):
        """Test resolving invalid state returns None."""
        resolver = GeographyResolver(metadata_cache)
        fips = resolver._resolve_state("InvalidState")
        assert fips is None
    
    def test_match_name_exact(self, metadata_cache):
        """Test exact name matching."""
        resolver = GeographyResolver(metadata_cache)
        name_to_fips = {"Wayne County": "163", "Oakland County": "125"}
        
        result = resolver._match_name("Wayne County", name_to_fips)
        assert result == "163"
    
    def test_match_name_fuzzy(self, metadata_cache):
        """Test fuzzy name matching."""
        resolver = GeographyResolver(metadata_cache)
        name_to_fips = {"Wayne County": "163"}
        
        result = resolver._match_name("Wayne", name_to_fips, strip_suffix="county")
        assert result == "163"
    
    def test_match_name_case_insensitive(self, metadata_cache):
        """Test name matching is case insensitive."""
        resolver = GeographyResolver(metadata_cache)
        name_to_fips = {"Wayne County": "163"}
        
        result = resolver._match_name("wayne county", name_to_fips)
        assert result == "163"
    
    @pytest.mark.asyncio
    async def test_resolve_state_geography(self, metadata_cache):
        """Test resolving state-level geography."""
        resolver = GeographyResolver(metadata_cache)
        
        async with aiohttp.ClientSession() as session:
            spec = await resolver.resolve(
                {"state": "Michigan"},
                session,
                year=2022
            )
        
        assert spec.level == GeographyLevel.STATE
        assert spec.fips_code == "26"
        assert "Michigan" in spec.name
    
    @pytest.mark.asyncio
    async def test_resolve_wildcard_state(self, metadata_cache):
        """Test resolving wildcard state."""
        resolver = GeographyResolver(metadata_cache)
        
        async with aiohttp.ClientSession() as session:
            spec = await resolver.resolve(
                {"state": "*"},
                session,
                year=2022
            )
        
        assert spec.fips_code == "*"
        assert "All States" in spec.name


# =============================================================================
# CLIENT TESTS
# =============================================================================

class TestACSClient:
    """Tests for ACSClient class."""
    
    @pytest.mark.asyncio
    async def test_client_context_manager(self, config_no_key):
        """Test client as async context manager."""
        async with ACSClient(config_no_key) as client:
            assert client._session is not None
            assert not client._session.closed
        
        # Session should be closed after exiting
        assert client._session.closed
    
    @pytest.mark.asyncio
    async def test_client_manual_close(self, config_no_key):
        """Test manual client lifecycle management."""
        client = ACSClient(config_no_key)
        await client._ensure_session()
        assert client._session is not None
        
        await client.close()
        assert client._session.closed
    
    @pytest.mark.asyncio
    async def test_build_url(self, config_no_key):
        """Test URL building."""
        client = ACSClient(config_no_key)
        url = client._build_url(2022, "acs/acs5")
        assert url == f"{CENSUS_API_BASE}/2022/acs/acs5"
    
    @pytest.mark.asyncio
    async def test_add_api_key_with_key(self, config_with_key):
        """Test API key is added to params."""
        client = ACSClient(config_with_key)
        params = {"get": "NAME"}
        params = client._add_api_key(params)
        assert params["key"] == "test_api_key_12345"
    
    @pytest.mark.asyncio
    async def test_add_api_key_without_key(self, config_no_key):
        """Test no key added when not configured."""
        client = ACSClient(config_no_key)
        params = {"get": "NAME"}
        params = client._add_api_key(params)
        assert "key" not in params
    
    @pytest.mark.asyncio
    async def test_list_tables_mock(self, config_no_key):
        """Test listing tables with mocked API response."""
        mock_response = {
            "groups": {
                "B01001": {"description": "SEX BY AGE", "universe": "Total population"},
                "B01003": {"description": "TOTAL POPULATION", "universe": "Total population"},
            }
        }
        
        with aioresponses() as m:
            m.get(
                f"{CENSUS_API_BASE}/2022/acs/acs5/groups.json",
                payload=mock_response
            )
            
            async with ACSClient(config_no_key) as client:
                # Clear cache to force API call
                client.cache.clear()
                tables = await client.list_tables(year=2022)
            
            assert len(tables) == 2
            table_ids = [t.table_id for t in tables]
            assert "B01001" in table_ids
            assert "B01003" in table_ids
    
    @pytest.mark.asyncio
    async def test_get_table_info_mock(self, config_no_key):
        """Test getting table info with mocked API response."""
        mock_response = {
            "description": "SEX BY AGE",
            "universe": "Total population",
            "variables": {
                "B01001_001E": {"label": "Estimate!!Total:", "concept": "SEX BY AGE"},
                "B01001_002E": {"label": "Estimate!!Total:!!Male:", "concept": "SEX BY AGE"},
                "NAME": {"label": "Geographic Area Name"},
            }
        }
        
        with aioresponses() as m:
            m.get(
                f"{CENSUS_API_BASE}/2022/acs/acs5/groups/B01001.json",
                payload=mock_response
            )
            
            async with ACSClient(config_no_key) as client:
                client.cache.clear()
                table_info = await client.get_table_info("B01001", year=2022)
            
            assert table_info is not None
            assert table_info.table_id == "B01001"
            assert table_info.title == "SEX BY AGE"
            assert len(table_info.variables) == 2  # NAME should be filtered out
    
    @pytest.mark.asyncio
    async def test_search_tables(self, config_no_key):
        """Test searching tables."""
        mock_groups = {
            "groups": {
                "B19013": {"description": "MEDIAN HOUSEHOLD INCOME", "universe": "Households"},
                "B19001": {"description": "HOUSEHOLD INCOME", "universe": "Households"},
                "B01001": {"description": "SEX BY AGE", "universe": "Total population"},
            }
        }
        
        with aioresponses() as m:
            m.get(
                f"{CENSUS_API_BASE}/2022/acs/acs5/groups.json",
                payload=mock_groups
            )
            
            async with ACSClient(config_no_key) as client:
                client.cache.clear()
                results = await client.search_tables("income", year=2022)
            
            # Income-related tables should be returned
            assert len(results) >= 2
            table_ids = [t.table_id for t in results]
            assert "B19013" in table_ids
            assert "B19001" in table_ids
    
    @pytest.mark.asyncio
    async def test_download_table_mock(self, config_no_key, temp_cache_dir):
        """Test downloading a table with mocked API response."""
        # Mock table info response
        mock_table_info = {
            "description": "TOTAL POPULATION",
            "universe": "Total population",
            "variables": {
                "B01003_001E": {"label": "Estimate!!Total:", "concept": "TOTAL POPULATION"},
            }
        }
        
        # Mock data response
        mock_data = [
            ["NAME", "B01003_001E", "B01003_001M", "state"],
            ["Michigan", "10037261", "null", "26"]
        ]
        
        with aioresponses() as m:
            # Mock table info endpoint
            m.get(
                f"{CENSUS_API_BASE}/2022/acs/acs5/groups/B01003.json",
                payload=mock_table_info
            )
            
            # Mock data endpoint - use regex pattern to match URL with query params
            m.get(
                re.compile(r'^https://api\.census\.gov/data/2022/acs/acs5\?'),
                payload=mock_data
            )
            
            async with ACSClient(config_no_key) as client:
                client.cache.clear()
                result = await client.download_table(
                    table_id="B01003",
                    geography={"state": "Michigan"},
                    year=2022,
                    output_dir=temp_cache_dir
                )
            
            assert result.success is True
            assert result.file_path is not None
            assert result.file_path.exists()
            assert result.rows == 1
    
    @pytest.mark.asyncio
    async def test_validate_table(self, config_no_key):
        """Test table validation."""
        mock_response = {
            "description": "SEX BY AGE",
            "variables": {}
        }
        
        with aioresponses() as m:
            m.get(
                f"{CENSUS_API_BASE}/2022/acs/acs5/groups/B01001.json",
                payload=mock_response
            )
            m.get(
                f"{CENSUS_API_BASE}/2022/acs/acs5/groups/INVALID.json",
                status=404
            )
            
            async with ACSClient(config_no_key) as client:
                client.cache.clear()
                
                valid = await client.validate_table("B01001", year=2022)
                assert valid is True
                
                invalid = await client.validate_table("INVALID", year=2022)
                assert invalid is False


# =============================================================================
# INTEGRATION-STYLE TESTS (with mocks)
# =============================================================================

class TestIntegration:
    """Integration-style tests with comprehensive mocking."""
    
    @pytest.mark.asyncio
    async def test_full_workflow_state_download(self, config_no_key, temp_cache_dir):
        """Test complete workflow: search -> info -> download."""
        # Mock responses
        mock_groups = {
            "groups": {
                "B01003": {"description": "TOTAL POPULATION", "universe": "Total population"},
            }
        }
        
        mock_table_info = {
            "description": "TOTAL POPULATION",
            "universe": "Total population",
            "variables": {
                "B01003_001E": {"label": "Estimate!!Total:", "concept": "TOTAL POPULATION"},
            }
        }
        
        mock_data = [
            ["NAME", "B01003_001E", "state"],
            ["Michigan", "10037261", "26"]
        ]
        
        with aioresponses() as m:
            # Set up all mock endpoints
            m.get(f"{CENSUS_API_BASE}/2022/acs/acs5/groups.json", payload=mock_groups)
            m.get(f"{CENSUS_API_BASE}/2022/acs/acs5/groups/B01003.json", payload=mock_table_info)
            # Use regex pattern to match URL with query params
            m.get(
                re.compile(r'^https://api\.census\.gov/data/2022/acs/acs5\?'),
                payload=mock_data
            )
            
            async with ACSClient(config_no_key) as client:
                client.cache.clear()
                
                # Step 1: Search for tables
                tables = await client.search_tables("population", year=2022)
                assert len(tables) > 0
                
                # Step 2: Get table info
                table_id = tables[0].table_id
                info = await client.get_table_info(table_id, year=2022)
                assert info is not None
                
                # Step 3: Download the table
                result = await client.download_table(
                    table_id=table_id,
                    geography={"state": "MI"},
                    year=2022,
                    output_dir=temp_cache_dir
                )
                
                assert result.success is True
                assert result.file_path.exists()


# =============================================================================
# ERROR HANDLING TESTS
# =============================================================================

class TestErrorHandling:
    """Tests for error handling scenarios."""
    
    @pytest.mark.asyncio
    async def test_invalid_state_raises_error(self, config_no_key):
        """Test that invalid state raises ValueError."""
        async with ACSClient(config_no_key) as client:
            with pytest.raises(ValueError, match="Could not resolve state"):
                await client.geo_resolver.resolve(
                    {"state": "InvalidStateName"},
                    client._session
                )
    
    @pytest.mark.asyncio
    async def test_county_without_state_raises_error(self, config_no_key):
        """Test that county without state raises ValueError."""
        async with ACSClient(config_no_key) as client:
            with pytest.raises(ValueError, match="requires state"):
                await client.geo_resolver.resolve(
                    {"county": "Wayne County"},
                    client._session
                )
    
    @pytest.mark.asyncio
    async def test_download_handles_api_error(self, config_no_key, temp_cache_dir):
        """Test download handles API errors gracefully."""
        mock_table_info = {
            "description": "TEST",
            "variables": {}
        }
        
        with aioresponses() as m:
            m.get(f"{CENSUS_API_BASE}/2022/acs/acs5/groups/B01001.json", payload=mock_table_info)
            # Use regex pattern to match URL with query params
            m.get(
                re.compile(r'^https://api\.census\.gov/data/2022/acs/acs5\?'),
                status=500
            )
            
            async with ACSClient(config_no_key) as client:
                client.cache.clear()
                result = await client.download_table(
                    table_id="B01001",
                    geography={"state": "MI"},
                    year=2022,
                    output_dir=temp_cache_dir
                )
            
            assert result.success is False
            assert result.error is not None


# =============================================================================
# RUN TESTS
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

