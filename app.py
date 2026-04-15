from flask import Flask, render_template, request, jsonify
from datetime import datetime, timedelta

app = Flask(__name__, template_folder='.')

busy_slots = []
_counter = 0
MAX_SEARCH_DAYS = 60


def time_to_minutes(t: str) -> int:
    h, m = map(int, t.split(':'))
    return h * 60 + m


def minutes_to_time(mins: int) -> str:
    if mins <= 0:
        return '00:00'
    if mins >= 1440:
        return '24:00'
    return f'{mins // 60:02d}:{mins % 60:02d}'


def merge_slots(slots):
    if not slots:
        return []
    s = sorted(slots, key=lambda x: x[0])
    merged = [list(s[0])]
    for start, end in s[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [tuple(x) for x in merged]


def normalize_repeat_days(days):
    if not days:
        return []
    return sorted({int(d) for d in days if 0 <= int(d) <= 6})


def slots_for_date(date_str: str):
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    dow = dt.weekday()
    out = []
    for s in busy_slots:
        repeat_days = s.get('repeatDays', [])
        if repeat_days:
            if dow in repeat_days:
                out.append({**s, 'date': date_str})
        elif s.get('date') == date_str:
            out.append(s)
    return out


def compute_free_blocks(start_dt_str: str, total_hours: float, max_days: int = MAX_SEARCH_DAYS):
    start_dt = datetime.fromisoformat(start_dt_str)
    total_minutes = max(0, round(total_hours * 60))
    allocated = []
    remaining = total_minutes
    current_dt = start_dt

    for _ in range(max_days):
        if remaining <= 0:
            break

        date_str = current_dt.strftime('%Y-%m-%d')
        start_min = current_dt.hour * 60 + current_dt.minute
        merged_busy = merge_slots([
            (s['startMinutes'], s['endMinutes'])
            for s in slots_for_date(date_str)
        ])

        free_gaps = []
        cursor = start_min
        for bstart, bend in merged_busy:
            if bend <= cursor:
                continue
            if bstart > cursor:
                free_gaps.append((cursor, bstart))
            cursor = max(cursor, bend)
        if cursor < 1440:
            free_gaps.append((cursor, 1440))

        for gstart, gend in free_gaps:
            if remaining <= 0:
                break
            available = gend - gstart
            if available <= 0:
                continue
            use = min(available, remaining)
            dh, dm = divmod(use, 60)
            duration_str = f'{dh}h {dm}m' if dh and dm else f'{dh}h' if dh else f'{dm}m'
            allocated.append({
                'date': date_str,
                'start': minutes_to_time(gstart),
                'end': minutes_to_time(gstart + use),
                'startMinutes': gstart,
                'endMinutes': gstart + use,
                'duration': use / 60,
                'durationStr': duration_str,
            })
            remaining -= use

        next_day = (current_dt + timedelta(days=1)).date()
        current_dt = datetime(next_day.year, next_day.month, next_day.day)

    return {
        'allocated': allocated,
        'totalAllocated': (total_minutes - remaining) / 60,
        'requested': total_hours,
        'fulfilled': remaining <= 0,
        'missing': remaining / 60,
    }


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/slots', methods=['GET'])
def get_slots():
    date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    return jsonify(slots_for_date(date))


@app.route('/api/slots', methods=['POST'])
def add_slot():
    global _counter
    d = request.json or {}
    start = d.get('start')
    end = d.get('end')
    if not start or not end:
        return jsonify({'error': 'Start and end are required'}), 400

    start_m = time_to_minutes(start)
    end_m = time_to_minutes(end)
    if end_m <= start_m:
        return jsonify({'error': 'End must be after start'}), 400

    repeat_days = normalize_repeat_days(d.get('repeatDays', []))
    slot = {
        'id': _counter,
        'date': None if repeat_days else d.get('date'),
        'start': start,
        'end': end,
        'startMinutes': start_m,
        'endMinutes': end_m,
        'label': d.get('label', 'Busy'),
        'color': d.get('color', '#d81b60'),
        'repeatDays': repeat_days,
    }
    busy_slots.append(slot)
    _counter += 1
    return jsonify(slot), 201


@app.route('/api/slots/<int:slot_id>', methods=['PUT'])
def update_slot(slot_id):
    slot = next((s for s in busy_slots if s['id'] == slot_id), None)
    if not slot:
        return jsonify({'error': 'Not found'}), 404

    d = request.json or {}
    start = d.get('start')
    end = d.get('end')
    if not start or not end:
        return jsonify({'error': 'Start and end are required'}), 400

    start_m = time_to_minutes(start)
    end_m = time_to_minutes(end)
    if end_m <= start_m:
        return jsonify({'error': 'End must be after start'}), 400

    repeat_days = normalize_repeat_days(d.get('repeatDays', slot.get('repeatDays', [])))
    slot.update({
        'date': None if repeat_days else d.get('date', slot.get('date')),
        'start': start,
        'end': end,
        'startMinutes': start_m,
        'endMinutes': end_m,
        'label': d.get('label', slot.get('label', 'Busy')),
        'color': d.get('color', slot.get('color', '#d81b60')),
        'repeatDays': repeat_days,
    })
    return jsonify(slot)


@app.route('/api/slots/<int:slot_id>', methods=['DELETE'])
def delete_slot(slot_id):
    global busy_slots
    busy_slots = [s for s in busy_slots if s['id'] != slot_id]
    return jsonify({'success': True})


@app.route('/api/free', methods=['GET'])
def get_free():
    start_dt = request.args.get('start_dt', datetime.now().isoformat(timespec='minutes'))
    hours = float(request.args.get('hours', 1))
    return jsonify(compute_free_blocks(start_dt, hours))


if __name__ == '__main__':
    today = datetime.now().strftime('%Y-%m-%d')
    busy_slots += [
        {
            'id': 0,
            'date': today,
            'start': '09:30',
            'end': '11:00',
            'startMinutes': 570,
            'endMinutes': 660,
            'label': 'Team Standup',
            'color': '#d81b60',
            'repeatDays': [],
        },
        {
            'id': 1,
            'date': None,
            'start': '13:00',
            'end': '14:00',
            'startMinutes': 780,
            'endMinutes': 840,
            'label': 'Lunch Break',
            'color': '#e65100',
            'repeatDays': [0, 1, 2, 3, 4],
        },
    ]
    _counter = 2
    app.run(debug=True, port=5000)
