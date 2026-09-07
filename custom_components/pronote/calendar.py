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
    tz = dt_util.get_time_zone(timezone)

    lesson_name = format_displayed_lesson(lesson)
    if lesson.canceled:
        lesson_name = f"Annulé - {lesson_name}"

    room = f"Salle {lesson.classroom}" if lesson.classroom else None
    description = " - ".join(part for part in (lesson.teacher_name, room) if part)

    return CalendarEvent(
        summary=lesson_name,
        description=description or None,
        location=room,
        start=lesson.start.replace(tzinfo=tz),
        end=lesson.end.replace(tzinfo=tz),
        # Without a uid every exported VEVENT carries UID "none".
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
        """The lesson that is running, or the next one, as of now."""
        lessons = self.coordinator.data.get("lessons_period")
        if lessons is None:
            return None

        # Lesson times are naive local time.
        now = dt_util.now().replace(tzinfo=None)

        # start >= now skipped the lesson already running.
        ongoing_or_next = [
            lesson for lesson in lessons if lesson.end > now and not lesson.canceled
        ]
        if not ongoing_or_next:
            return None

        # pronotepy never sorts the lessons it appends.
        return async_get_calendar_event_from_lessons(
            min(ongoing_or_next, key=lambda lesson: lesson.start),
            self.hass.config.time_zone,
        )

    @callback
    def _async_write_ha_state(self) -> None:
        """Recompute on every state write: CalendarEntity wakes up at the end
        of self.event and then schedules nothing further.
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
        # The key is None when the lesson fetch failed.
        lessons = self.coordinator.data.get("lessons_period") or []
        events = [
            async_get_calendar_event_from_lessons(lesson, hass.config.time_zone)
            for lesson in lessons
            if not lesson.canceled
        ]
        # Overlap, not containment: a straddling lesson belongs to both.
        return [
            event
            for event in events
            if event.start < end_date and event.end > start_date
        ]
