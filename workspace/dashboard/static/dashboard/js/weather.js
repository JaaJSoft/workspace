/**
 * The weather next to the dashboard's date: Open-Meteo's current reading at
 * the browser's position, in the user's temperature unit.
 *
 * Day or night comes from the forecast's `is_day`, computed for the position
 * it was read at - never from a clock, whose zone may not be the weather's.
 */

const WEATHER_CACHE_KEY = 'dashboard_weather';
const WEATHER_CACHE_TTL = 30 * 60 * 1000;

// WMO weather codes as Open-Meteo reports them.
const WEATHER_CONDITIONS = {
  0: 'clear', 1: 'clear', 2: 'partly-cloudy', 3: 'cloudy',
  45: 'fog', 48: 'fog',
  51: 'drizzle', 53: 'drizzle', 55: 'drizzle', 56: 'drizzle', 57: 'drizzle',
  61: 'rain', 63: 'rain', 65: 'rain', 66: 'rain', 67: 'rain',
  71: 'snow', 73: 'snow', 75: 'snow', 77: 'snow',
  80: 'rain', 81: 'rain', 82: 'rain',
  85: 'snow', 86: 'snow',
  95: 'thunderstorm', 96: 'thunderstorm', 99: 'thunderstorm',
};
const WEATHER_ICONS = {
  clear: 'sun',
  'partly-cloudy': 'cloud-sun',
  cloudy: 'cloud',
  fog: 'cloud-fog',
  drizzle: 'cloud-drizzle',
  rain: 'cloud-rain',
  snow: 'snowflake',
  thunderstorm: 'cloud-lightning',
};
const WEATHER_NIGHT_ICONS = {
  clear: 'moon',
  'partly-cloudy': 'cloud-moon',
};

function weatherIcon(code, isDay) {
  const condition = WEATHER_CONDITIONS[code] || 'cloudy';
  return (!isDay && WEATHER_NIGHT_ICONS[condition]) || WEATHER_ICONS[condition];
}

function forecastUrl(coords, unit) {
  return 'https://api.open-meteo.com/v1/forecast'
    + '?latitude=' + coords.latitude.toFixed(2)
    + '&longitude=' + coords.longitude.toFixed(2)
    + '&current=temperature_2m,weather_code,is_day'
    + '&temperature_unit=' + encodeURIComponent(unit);
}

// The cached reading, when it is recent and in `unit`. An entry written
// before the unit and `is_day` were stored has neither and is read again.
function cachedWeather(unit) {
  try {
    const reading = JSON.parse(localStorage.getItem(WEATHER_CACHE_KEY));
    if (reading && reading.unit === unit && typeof reading.isDay === 'boolean'
        && reading.ts + WEATHER_CACHE_TTL > Date.now()) {
      return reading;
    }
  } catch (e) { /* storage blocked or entry unreadable: read the forecast */ }
  return null;
}

function cacheWeather(reading) {
  try {
    localStorage.setItem(WEATHER_CACHE_KEY, JSON.stringify(reading));
  } catch (e) { /* storage blocked: the next visit reads the forecast again */ }
}

window.weatherWidget = function weatherWidget(unit) {
  return {
    unit: unit,
    date: '',
    label: '',
    icon: '',
    init() {
      this.date = new Date().toLocaleDateString(window.getUserLocale(), {
        timeZone: window.getUserTimeZone(),
        weekday: 'long',
        month: 'long',
        day: 'numeric',
      });
      this.label = this.date;
      this.load();
    },
    setUnit(unit) {
      if (unit === this.unit) return;
      this.unit = unit;
      this.load();
    },
    load() {
      const cached = cachedWeather(this.unit);
      if (cached) {
        this.show(cached);
        return;
      }
      if (!navigator.geolocation) return;
      const unit = this.unit;
      navigator.geolocation.getCurrentPosition((position) => {
        fetch(forecastUrl(position.coords, unit))
          .then((response) => response.json())
          .then((data) => {
            const reading = {
              temp: Math.round(data.current.temperature_2m),
              symbol: data.current_units.temperature_2m,
              code: data.current.weather_code,
              isDay: data.current.is_day === 1,
              unit: unit,
              ts: Date.now(),
            };
            cacheWeather(reading);
            // The unit changed while this one was being read.
            if (unit === this.unit) this.show(reading);
          })
          .catch(() => {});
      }, () => {}, { timeout: 5000 });
    },
    show(reading) {
      this.label = this.date + ' · ' + reading.temp + reading.symbol;
      this.icon = weatherIcon(reading.code, reading.isDay);
    },
  };
};
