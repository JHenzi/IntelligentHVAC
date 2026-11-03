# Dashboard

![Dashboard](../documentation/images/dashboard.png)

Built with **core cards only** — no HACS frontend cards required.

## Install

Requires the [comfort package](../packages/README.md) (it provides the helpers the dashboard shows).

1. Settings → Dashboards → **Add dashboard** → open it → ⋮ → **Raw configuration editor**.
2. Paste the contents of `intelligent_hvac_dashboard.yaml` and save.
3. If your entity IDs differ from the defaults (`climate.thermostat` etc.), find & replace them.

## Using it

- **Wake-up time / Target temperature** — set when and how warm you want to wake up.
- **Recovery Time Calculations** — the three gauges show the model's base time, the degrees to recover, and the scaled result.
- **Update Recovery Now** — recompute immediately. **Test** buttons run the heat-up/setback automations for real, so they change your thermostat.
