# Historical weather integration notes

Historical hourly temperature and precipitation can provide context for changes in electricity use. Weather alignment must use a consistent timezone and the same event window as the meter analysis. Weather correlation is supporting evidence rather than proof of causation.

If the weather service times out, returns no observations, or provides an incomplete interval, an investigation should record the limitation and reduce confidence. It must not invent a temperature or claim that weather explains the anomaly.

This is a short MeterDetective-authored integration summary. The linked Open-Meteo documentation defines the API fields and provider attribution requirements.
