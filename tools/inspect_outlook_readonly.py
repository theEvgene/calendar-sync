"""Read-only, aggregate Outlook COM preflight. Never prints event content or IDs."""
from datetime import datetime, timedelta
import json
import win32com.client


def main():
    folder = win32com.client.Dispatch('Outlook.Application').GetNamespace('MAPI').GetDefaultFolder(9)
    items = folder.Items
    items.Sort('[Start]')
    items.IncludeRecurrences = True
    start = datetime.now()
    end = start + timedelta(days=30)
    selected = items.Restrict(
        f"[Start] < '{end:%m/%d/%Y %I:%M %p}' AND [End] > '{start:%m/%d/%Y %I:%M %p}'"
    )
    counts = {'default_folder_accessible': True, 'appointment_count': 0,
              'recurring_instances': 0, 'all_day_instances': 0,
              'utc_fields_readable': 0, 'global_id_readable': 0}
    for item in selected:
        if item.Class != 26:
            continue
        counts['appointment_count'] += 1
        counts['recurring_instances'] += bool(item.IsRecurring)
        counts['all_day_instances'] += bool(item.AllDayEvent)
        counts['utc_fields_readable'] += bool(item.StartUTC and item.EndUTC)
        counts['global_id_readable'] += bool(item.GlobalAppointmentID)
    print(json.dumps(counts))


if __name__ == '__main__':
    main()
