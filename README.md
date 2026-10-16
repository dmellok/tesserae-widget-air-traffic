# air-traffic widget for Tesserae

Flights currently overhead at your location via the [OpenSky Network](https://opensky-network.org/) public API. Shows the total count plus the nearest N flights with callsign, altitude, speed, and distance. No API key.

Set the cell's `latitude` / `longitude` once and the widget polls OpenSky's `states/all` endpoint within the configured radius.

The small tag after each callsign is the aircraft's registration country, which is all OpenSky carries. Turn on **Show route** to look each callsign up on [adsbdb.com](https://www.adsbdb.com/) (free, no key) and show the departure and arrival airports instead, as `SYD→MEL`. Scheduled flights resolve; private aircraft flying their registration as the callsign keep the country. Routes are cached for a week per callsign, so the lookup costs a request per new callsign, not per refresh.

## Options

| Option | Default | Notes |
| --- | --- | --- |
| Latitude / Longitude | | Centre of the search. |
| Search radius (km) | 60 | Flights beyond this are counted but not listed. |
| Max flights to list | 8 | Nearest first. |
| Layout | Vertical | Radar above the list, or beside it. |
| Show Numeric Labels | off | Numbers each plane on the radar and in the list. |
| Show route | off | Departure→arrival from adsbdb.com in place of the country. |

Install via Settings → Widgets → Browse community widgets on [Tesserae](https://github.com/dmellok/tesserae).

## Folders shipped

- `sky_air_traffic`

Previously bundled in [`tesserae-sky`](https://github.com/dmellok/tesserae-sky); carved into its own repo for v0.1.2+ so users who only want flights don't have to pull the rest of the sky bundle.

## License

AGPL-3.0-or-later. See [LICENSE](./LICENSE).
