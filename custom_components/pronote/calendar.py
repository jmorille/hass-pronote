from datetime import datetime
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.util import dt as dt_util

from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .coordinator import PronoteDataUpdateCoordinator
from .pronote_formatter import format_displayed_lesson

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Pronote calendar based on a config entry."""
    coordinator: PronoteDataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]["coordinator"]

    if coordinator.data is None or coordinator.data.get("child_info") is None:
        return

    async_add_entities([PronoteCalendar(coordinator, config_entry)], False)


@callback
def async_get_calendar_event_from_lessons(lesson, timezone) -> CalendarEvent:
    """Get a HASS CalendarEvent from a Pronote Lesson."""
    # get_time_zone is Home Assistant's memoised lookup; ZoneInfo() reads the
    # tz database from disk, and this runs on the event loop.
    tz = dt_util.get_time_zone(timezone)

    lesson_name = format_displayed_lesson(lesson)
    if lesson.canceled:
        lesson_name = f"Annulé - {lesson_name}"

    return CalendarEvent(
        summary=lesson_name,
        description=f"{lesson.teacher_name} - Salle {lesson.classroom}",
        location=f"Salle {lesson.classroom}",
        start=lesson.start.replace(tzinfo=tz),
        end=lesson.end.replace(tzinfo=tz),
        # Without a uid, CalendarEvent leaves the field at None and every
        # exported VEVENT carries UID "none": an ICS consumer then sees the
        # whole timetable as one event repeated and keeps only the last one.
        # Lesson.id is Pronote's own per-lesson identifier, and pronotepy
        # resolves it strictly, so a Lesson that exists always has one.
        uid=lesson.id,
    )


class PronoteCalendar(CoordinatorEntity, CalendarEntity):

    def __init__(
        self,
        coordinator: PronoteDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the ReCollect Waste entity."""
        super().__init__(coordinator, entry)

        child_info = coordinator.data["child_info"]
        calendar_name = child_info.name
        nickname = self.coordinator.config_entry.options.get("nickname", "")
        if nickname != "":
            calendar_name = nickname

        self._attr_translation_key = "timetable"
        self._attr_translation_placeholders = {"child": calendar_name}
        self._attr_unique_id = f"{coordinator.data['sensor_prefix']}-timetable"
        self._attr_device_info = DeviceInfo(
            name=f"Pronote - {self.coordinator.data['child_info'].name}",
            identifiers={(DOMAIN, self.coordinator.data["child_info"].name)},
            manufacturer="Pronote",
            model=self.coordinator.data["child_info"].name,
        )
        self._event: CalendarEvent | None = None

    @property
    def event(self) -> CalendarEvent | None:
        """Return the next upcoming event."""
        return self._event

    @callback
    def _compute_event(self) -> CalendarEvent | None:
        """The lesson that is running, or the next one, as of now.

        Pure: it reads the coordinator data and returns a value, so it can be
        called from anywhere a state is about to be written.
        """
        lessons = self.coordinator.data.get("lessons_period")
        if lessons is None:
            # The key is None whenever the lesson fetch failed. Reporting no
            # event is right: the entity must not keep announcing a lesson
            # from data the coordinator no longer stands behind.
            return None

        # dt_util.now() is the time in the zone Home Assistant is configured
        # for. datetime.now() was the host's, UTC on a default container, which
        # shifted the whole selection by an hour or two. Lesson times are naive
        # local, so the offset comes straight back off for the comparison.
        now = dt_util.now().replace(tzinfo=None)

        # `event.start >= now` implies `now < event.end`, so the second test was
        # dead and this picked the *next* lesson even while one was running: at
        # the first refresh after a lesson started, the entity dropped back to
        # off and stayed there. Cancelled lessons go out here too -
        # async_get_events already drops them, and the entity must not
        # contradict the panel about what is on the calendar.
        ongoing_or_next = [
            lesson for lesson in lessons if lesson.end > now and not lesson.canceled
        ]
        if not ongoing_or_next:
            return None

        # min() rather than next(): pronotepy appends lessons week by week in
        # Pronote's own order and never sorts them, so "the first one that
        # matches" was not the earliest one.
        return async_get_calendar_event_from_lessons(
            min(ongoing_or_next, key=lambda lesson: lesson.start),
            self.hass.config.time_zone,
        )

    @callback
    def _async_write_ha_state(self) -> None:
        """Recompute the event on every state write, not only on new data.

        This replaces the `_handle_coordinator_update` override that used to
        do the selection. Every path that publishes a state ends up here -
        `CoordinatorEntity._handle_coordinator_update` calls
        `async_write_ha_state`, and so do the calendar's own alarms - so one
        recompute here covers all of them, with no second place to keep in
        step.

        `CalendarEntity._async_write_ha_state` schedules a wake-up at the end
        of `self.event` and, when that fires, finds `now >= event.end`, so it
        schedules nothing further (homeassistant/components/calendar,
        `_async_write_ha_state`). Recomputing only from the coordinator left
        two holes, both measured on a live instance:

        - back-to-back lessons: at 10:30:00 the end alarm fired, the entity
          still held the 09:30-10:30 lesson, so it wrote `off` and went quiet
          until the next refresh - 10:35:46. Six minutes of `off` with a
          lesson in progress, at every changeover of the day.
        - after a restart or an options reload: `CoordinatorEntity`
          registers the listener without calling it, so the first state write
          had `self._event` still at None and the calendar read `off` with no
          attributes until the next refresh - a whole interval, 15 minutes by
          default and more if the user lengthened it.

        Recomputing here closes both. The end alarm now finds the following
        lesson, writes `on` and schedules that lesson's end, so the chain
        carries itself between refreshes; and the first write of a fresh
        entity already has the right event.

        This must stay synchronous and must not raise: it runs inside the
        state write. `_compute_event` only reads already-fetched data.
        """
        self._event = self._compute_event()
        super()._async_write_ha_state()

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return calendar events within a datetime range."""
        # .get(): the key is None whenever the lesson fetch failed, and this is
        # called by the calendar component without consulting `available` - so
        # opening the panel during an outage raised TypeError at the websocket.
        lessons = self.coordinator.data.get("lessons_period") or []
        events = [
            async_get_calendar_event_from_lessons(lesson, hass.config.time_zone)
            for lesson in lessons
            if not lesson.canceled
        ]
        # The range was ignored, so every lesson the coordinator held was
        # returned whatever the caller asked for: the panel drew a fortnight of
        # lessons into any week, and `calendar.get_events` answered with events
        # outside its own window. Overlap, not containment - a lesson that
        # straddles the boundary belongs to both.
        return [
            event
            for event in events
            if event.start < end_date and event.end > start_date
        ]
