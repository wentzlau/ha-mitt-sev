# ha-mitt-sev
Integration for Home Assistent that fetches values from [Mitt Sev](https://mittsev.fo) and expose them as sensors.

You must register your self at mittsev.fo and create an api key.

## Installation
### Via HACKS
Add this repository to HACKS via HACKS/user defined repositories
### Manual installation
1) Create a subfolder called mitt_sev in the .homeassistant/custom_components folder. 
2) Copy the contents of the mitt_sev/custom_components/ha-mitt-sev folder into the newly created subfolder.

## Configuration
Add the the sensor section below to configuration.yaml.

```
sensor:    
- platform: mitt_sev
  user_name: '[api user name]'
  api_key: '[api key]'
```
