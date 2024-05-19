"""Platform for sensor integration."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
import re

import aiohttp
import async_timeout

from collections import defaultdict

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from homeassistant.helpers.typing import HomeAssistantType, ConfigType
from homeassistant.components import sensor
from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import (
    CONF_MONITORED_CONDITIONS, CONF_API_KEY, CONF_LATITUDE, CONF_LONGITUDE,
    TEMP_FAHRENHEIT, TEMP_CELSIUS, LENGTH_INCHES,
    LENGTH_FEET, LENGTH_MILLIMETERS, LENGTH_METERS, SPEED_MILES_PER_HOUR, SPEED_KILOMETERS_PER_HOUR,
    PERCENTAGE, PRESSURE_INHG, PRESSURE_MBAR, PRECIPITATION_INCHES_PER_HOUR, PRECIPITATION_MILLIMETERS_PER_HOUR,
    ATTR_ATTRIBUTION)
from homeassistant.exceptions import PlatformNotReady
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import Throttle
import homeassistant.helpers.config_validation as cv
from homeassistant.util.unit_system import METRIC_SYSTEM

import voluptuous as vol
import json

_LOGGER = logging.getLogger("mitt_sev")

SEV_URL = "https://api.sev.fo/api/CustomerRESTApi/"
CONF_USER= "user_name"
CONF_API_KEY= "api_key"

MIN_TIME_BETWEEN_UPDATES = timedelta(minutes=30)
CONF_ATTRIBUTION = "Data provided by api.sev.fo"


class SevSensorConfig:
    """Sensor Configuration.
    defines basic HA properties of the energy sensor and
    stores callbacks that can parse sensor values out of
    the json data received by mitt sev API.
    """

    def __init__(self, friendly_name, value,
                 unit_of_measurement=None, entity_picture=None,
                 icon="mdi:gauge", device_state_attributes=None,
                 device_class=None):
        """Constructor.
        Args:
            friendly_name (string|func): Friendly name
            value (function(SEVData)): callback that
                extracts desired value from SEVData object
            unit_of_measurement (string): unit of measurement
            entity_picture (string): value or callback returning
                URL of entity picture
            icon (string): icon name
            device_state_attributes (dict): dictionary of attributes,
                or callable that returns it
        """
        self.friendly_name = friendly_name
        self.unit_of_measurement = unit_of_measurement
        self.value = value
        self.entity_picture = entity_picture
        self.icon = icon
        self.device_state_attributes = device_state_attributes or {}
        self.device_class = device_class
        


class EnergyCurrentConditionsSensorConfig(SevSensorConfig):
    """Helper for defining sensor configurations for current conditions."""

    def __init__(self, friendly_name, meter_id, sensor_type, icon="mdi:gauge",
                 unit_of_measurement=None, device_class=None):
        """Constructor.
        Args:
            friendly_name (string|func): Friendly name of sensor
            field (string): Field name in the "observations[0][unit_system]"
                            dictionary.
            icon (string): icon name , if None sensor
                           will use current weather symbol
            unit_of_measurement (string): unit of measurement
        """
        
        super().__init__(
            friendly_name,
            value=lambda wu: wu.data[meter_id][sensor_type]['value'],
            icon=icon,
            unit_of_measurement= unit_of_measurement,
            device_state_attributes={
                'date': lambda wu: wu.data[meter_id][sensor_type]['time']
            },
            device_class=device_class
        )

SENSOR_TYPES = {
    'kwh': {
        'name': 'Energy consumption, last hour',
        'unit_of_measurement': 'kwh',
        'icon': "mdi:home-lightning-bolt",
        'device_class': "power_factor",
        'state_class': "measurement",

    },
    'co2': {
        'name': 'Estimated co2 usage, last hour',
        'unit_of_measurement': 'kg',
        'icon':"mdi:molecule-co2",
        'device_class': "power_factor",
        'state_class': "measurement",
    },
    'cost': {
        'name': 'Estimated cost, last hour',
        'unit_of_measurement': 'kr',
        'icon': "mdi:circle-multiple",
        'device_class': "power_factor",
        'state_class': "measurement",
    },
    'kwh_today': {
        'name': 'Energy consumption, today',
        'unit_of_measurement': 'kwh',
        'icon': "mdi:home-lightning-bolt",
        'device_class': "power_factor",
        'state_class': "measurement",

    },
    'kwh_total': {
        'name': 'Energy consumption, cumulative',
        'unit_of_measurement': 'kwh',
        'icon': "mdi:home-lightning-bolt",
        'device_class': "power_factor",
        'state_class': "measurement",

    },
    'co2_today': {
        'name': 'Estimated co2 usage, today',
        'unit_of_measurement': 'kg',
        'icon':"mdi:molecule-co2",
        'device_class': "power_factor",
        'state_class': "measurement",
    },
    'cost_today': {
        'name': 'Estimated cost, today',
        'unit_of_measurement': 'kr',
        'icon': "mdi:circle-multiple",
        'device_class': "power_factor",
        'state_class': "measurement",
    }
}

METERS = []

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_USER): cv.string,
    vol.Required(CONF_API_KEY): cv.string
})

async def async_setup_platform(hass: HomeAssistantType, config: ConfigType,
                               async_add_entities, discovery_info=None):
    
    #_LOGGER.info("areas in config: %s", areas )
    user_name = config[CONF_USER]
    api_key = config[CONF_API_KEY]
    _LOGGER.info("mitt sev user: %s", user_name)

    sev_data = SEVData(hass, user_name, api_key)
 
    meters = await SEVData.async_meters(sev_data)
    if not meters:
        _LOGGER.warning("no sev installations found")
        return
    sensors = []
    for c in range(len(meters)):
        customer = meters[c]
        _LOGGER.info("customer: %s", customer["customer_name"] )
        for i in range(len(customer["installations"])):
            installation = customer["installations"][i]
            inst_id = installation["inst_id"] 
            rest = SEVData(hass, user_name, api_key)
            for m in range(len(installation["meters"])):
                meter = installation["meters"][m]
                meter_id = str(meter["meter_id"])
                meter_name = meter["meter_name"]
                meter_type = meter["meter_type"]
                _LOGGER.info("meter: %s : %s", meter_id, meter_name)
                METERS.append(meter_id)
                sensors.append(SevSensor(hass, rest, 'kwh', inst_id, meter_id, meter_name, meter_type))
                sensors.append(SevSensor(hass, rest, 'co2', inst_id, meter_id, meter_name, meter_type))
                sensors.append(SevSensor(hass, rest, 'cost', inst_id, meter_id, meter_name, meter_type))
                sensors.append(SevSensor(hass, rest, 'kwh_today', inst_id, meter_id, meter_name, meter_type))
                sensors.append(SevSensor(hass, rest, 'kwh_total', inst_id, meter_id, meter_name, meter_type))
                sensors.append(SevSensor(hass, rest, 'co2_today', inst_id, meter_id, meter_name, meter_type))
                sensors.append(SevSensor(hass, rest, 'cost_today', inst_id, meter_id, meter_name, meter_type))
            

    async_add_entities(sensors, True)

class SevSensor(SensorEntity):
    """Implementing the sev sensor."""

    def __init__(self, hass: HomeAssistantType, rest, sensor_type, inst_id, meter_id, meter_name, meter_type):
        """Initialize the sensor."""
        self.inst_id = inst_id
        self.meter_id = meter_id
        if meter_type=="E-01":
            self.meter_name = "Main meter"
        elif meter_type=="E-02":
            self.meter_name = "Green meter"
        else:
            self.meter_name = meter_name
        self.meter_type  = meter_type
        self.rest = rest
        self._sensor_type = sensor_type
        self._state = None
        self._state_class = "measurement"
        self._attributes = {
            ATTR_ATTRIBUTION: CONF_ATTRIBUTION,
        }
        self._icon = None
        self._entity_picture = None
        self._unit_of_measurement = self._cfg_expand("unit_of_measurement")
        # This is only the suggested entity id, it might get changed by
        # the entity registry later.
        unique_id = 'd_mitt_sev_' + str(inst_id) + '_' + str(meter_id) + '_'  + sensor_type
        self.entity_id = sensor.ENTITY_ID_FORMAT.format('mitt_sev_' + str(inst_id) + '_' + str(meter_id) + '-' + sensor_type)
        self._unique_id = unique_id
        self._device_class = self._cfg_expand("device_class")


    def _cfg_expand(self, what, default=None):
        """Parse and return sensor data."""
        sensor_info = SENSOR_TYPES[self._sensor_type]
        cfg = EnergyCurrentConditionsSensorConfig(
            self.meter_name + ", " + sensor_info['name'],
            meter_id = self.meter_id,
            sensor_type=self._sensor_type,
            icon = sensor_info['icon'],
            unit_of_measurement=sensor_info['unit_of_measurement'],
            device_class= sensor_info['device_class']
        )
        #SENSOR_TYPES[self._condition]
        val = getattr(cfg, what)
        if not callable(val):
            return val
        try:
            val = val(self.rest)
        except (KeyError, IndexError, TypeError, ValueError) as err:
            _LOGGER.warning("Failed to expand cfg from WU API."
                            " Condition: %s Attr: %s Error: %s",
                            self._sensor_type, what, repr(err))
            val = default

        return val

    def _update_attrs(self):
        """Parse and update device state attributes."""
        attrs = self._cfg_expand("device_state_attributes", {})

        for (attr, callback) in attrs.items():
            if callable(callback):
                try:
                    self._attributes[attr] = callback(self.rest)
                except (KeyError, IndexError, TypeError, ValueError) as err:
                    _LOGGER.warning("Failed to update attrs from WU API."
                                    " Condition: %s Attr: %s Error: %s",
                                    self._sensor_type, attr, repr(err))
            else:
                self._attributes[attr] = callback

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._cfg_expand("friendly_name")

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return self._attributes

    @property
    def icon(self):
        """Return icon."""
        return self._icon

    @property
    def entity_picture(self):
        """Return the entity picture."""
        return self._entity_picture

    @property
    def unit_of_measurement(self):
        """Return the units of measurement."""
        return self._unit_of_measurement

    @property
    def device_class(self):
        """Return the units of measurement."""
        return self._device_class

    @property
    def state_class(self):
        return self._state_class
    
    async def async_update(self):
        """Update current conditions."""
        _LOGGER.info("read sensor: %s", self.meter_id)
        await self.rest.async_update()

        if not self.rest.data:
            # no data, return
            return
        self._state = self._cfg_expand("value")
        self._update_attrs()
        self._icon = self._cfg_expand("icon", super().icon)
        url = self._cfg_expand("entity_picture")
        if isinstance(url, str):
            self._entity_picture = re.sub(r'^http://', 'https://',
                                          url, flags=re.IGNORECASE)

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return self._unique_id

class SEVData:
    """Get data from api.sev.fo"""
    def __init__(self, hass, user_id, api_key):
        """Initialize the data object."""
        self._hass = hass
        self._features = set()
        self.user_id = user_id
        self.api_key = api_key
        self.data = None
        self.token = None
        self._session = async_get_clientsession(self._hass)
    

    def tofloat(self, sval):
        return float(sval.replace(",", "."))

    
    async def async_get_token(self):
        try:
            _LOGGER.debug("get_token url: %s", SEV_URL + 'login_and_get_jwt_token')
            _LOGGER.debug("get_token: %s / %s", self.user_id, self.api_key)
            with async_timeout.timeout(10):
                sev_data = await self._session.post(
                    SEV_URL + 'login_and_get_jwt_token',
                    json={
                        "user_name": self.user_id,
                        "password": self.api_key
                    }
                )

                if sev_data is None:
                    raise ValueError('NO CURRENT RESULT')
                _LOGGER.debug("get token status: %s", sev_data.status)
                

        except ValueError as err:
            _LOGGER.error("Check sev energy API %s", err.args)
        except (asyncio.TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.error("Error fetching energy data: %s", repr(err))

        if sev_data and sev_data.status < 300:
            byte_data = bytearray()
            while not sev_data.content.at_eof():
                chunk = await sev_data.content.read(1024)
                byte_data += chunk   
            
            self.token = byte_data.decode('utf8')
        else:
            return None
    
    async def async_post(self, api, data):
        if not self.token:
            await self.async_get_token()
        if not self.token:
            return None
        _LOGGER.debug("post resuest: %s", SEV_URL + api)
        try:
            with async_timeout.timeout(10):
                sev_data = await self._session.post(
                    SEV_URL + api,
                    headers = {
                        'Authorization': 'Bearer ' + self.token
                    },
                    json = data
                )
                if sev_data is None:
                    raise ValueError('NO CURRENT RESULT')
                _LOGGER.debug("async_post: %s", sev_data.status)
                
        except ValueError as err:
            _LOGGER.error("Check sev energy API %s", err.args)
        except (asyncio.TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.error("Error fetching energy data: %s", repr(err))

        if sev_data and sev_data.status < 300:
            byte_data = bytearray()
            while not sev_data.content.at_eof():
                chunk = await sev_data.content.read(1024)
                byte_data += chunk   
            
            json_str =byte_data.decode('utf8')

            _LOGGER.debug("json response: %s", json_str)

            sev_data = json.loads(json_str)
            return sev_data
        else:
            return None
    async def async_meters(self):
        meter_data = await self.async_post(
            "get_available_meters",
            {}
        )

        return meter_data
    
    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    async def async_update(self):
        _LOGGER.info("SEVData async_update: %s" , METERS)
        date_from = datetime.today().strftime('%Y-%m-%dT00:00:00')
        date_to = datetime.today().strftime('%Y-%m-%dT%H:%M:%S')
        response ={
            "kwh": None,
            "co2": None,
            "cost": None
        }
        response["kwh"] = await self.async_post(
            "hourly_kwh_usage",
            data = {
                "meters": METERS,
                "from_date": date_from,
                "to_date": date_to
            }
        )
        response["co2"] = await self.async_post(
            "estimated_CO2",
            data = {
                "meters": METERS,
                "from_date": date_from,
                "to_date": date_to
            }
        )

        response["cost"] = await self.async_post(
            "estimated_cost",
            data = {
                "meters": METERS,
                "from_date": date_from,
                "to_date": date_to
            }
        )
        data = defaultdict(dict)
        for data_type in response.keys():
            _LOGGER.debug("ds %s", data_type)
            data_set = response[data_type]    
            if data_set:
                for meter in data_set:
                    meter_id = meter["meter_id"]
                    last = meter["readings"][-1]
                    value_sum = sum(r["reading"] for r in meter["readings"])
                    data[meter_id][data_type] = { 
                        "time": last["time_stamp"],
                        "value": last["reading"]
                    }
                    data[meter_id][data_type + '_today'] = { 
                        "time": last["time_stamp"],
                        "value": value_sum
                    }
                    if data_type=="kwh":
                        data[meter_id][data_type + '_total'] = { 
                        "time": last["time_stamp"],
                        "value": last["cumulative_value"]
                    }    
                
        _LOGGER.debug("sensor data: %s", data)
        self.data = data
        