# 오렌지듄스 영종 - 평일(월~금) 2부 빈자리 알림
# - PC: 1분마다 확인 -> 소리 + 팝업 + 예약 페이지 열기 + 폰 알림
# - 서버(GitHub): 5~15분마다 한 번 확인 -> 폰 알림  (python watch_2bu.py --once)
# PC와 서버는 "이미 알린 자리" 목록을 ntfy 비밀 주제로 같이 써서 같은 알림을 두 번 보내지 않음.
import json, os, sys, time, threading, datetime, urllib.request

CHECK_EVERY_SEC = 60        # PC에서 몇 초마다 확인할지
TARGET_PART = "2"           # 2부
WEEKDAYS = {"2", "3", "4", "5", "6"}  # dwCode: 2=월 ... 6=금
API = "https://api.orangedunesyj.com/v1/reservation-calender"
PAGE = "https://www.orangedunesyj.com/calendar"
DAY_KO = {"2": "월", "3": "화", "4": "수", "5": "목", "6": "금"}

# 폰 알림 주제 (ntfy 앱에서 구독). 서버에서는 환경변수 NTFY_TOPIC 으로 받음
HERE = os.path.dirname(os.path.abspath(__file__))


def _topic():
    t = os.environ.get("NTFY_TOPIC", "").strip()
    if t:
        return t
    try:  # PC에서는 같은 폴더의 topic.txt (인터넷에 올리지 않음)
        return open(os.path.join(HERE, "topic.txt"), encoding="utf-8").read().strip()
    except Exception:
        return ""


PHONE_TOPIC = _topic()
STATE_TOPIC = PHONE_TOPIC + "-state"   # 이미 알린 자리 목록 공유용 (구독 안 함)
KST = datetime.timezone(datetime.timedelta(hours=9))
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Origin": "https://www.orangedunesyj.com",
    "Referer": "https://www.orangedunesyj.com/",
}


def now_kst():
    return datetime.datetime.now(KST)


def get(date: str):
    y, m, _ = date.split("-")
    url = f"{API}?year={int(y)}&month={int(m)}&date={date}"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))["data"]["calenders"]


def open_weekdays():
    today = now_kst().date().isoformat()
    cal = get(today)
    return [c for c in cal
            if c.get("dwCode") in WEEKDAYS
            and c.get("availableStatusByDate") == "ING"
            and c["bsnDate"] >= today]


def find_2bu():
    found = {}
    for c in open_weekdays():
        d = c["bsnDate"]
        cal = get(d)
        day = next((x for x in cal if x["bsnDate"] == d), None)
        times = (day or {}).get("reservationTimeWithPrices") or []
        for t in times:
            if t.get("partDiv") == TARGET_PART:
                found[str(t["timeId"])] = f"{d}({DAY_KO[c['dwCode']]}) {t['resveTime']}"
        time.sleep(1)  # 서버에 부담 안 주게 조금씩
    return found


# ---------- 이미 알린 자리 목록 (PC/서버 공유) ----------
def load_shared():
    """ntfy 상태 주제의 가장 최근 목록. 없으면 None"""
    try:
        url = f"https://ntfy.sh/{STATE_TOPIC}/json?poll=1&since=12h"
        with urllib.request.urlopen(url, timeout=15) as r:
            lines = [l for l in r.read().decode("utf-8").splitlines() if l.strip()]
        msgs = [json.loads(l) for l in lines]
        msgs = [m for m in msgs if m.get("event") == "message"]
        if not msgs:
            return None
        last = max(msgs, key=lambda m: m["time"])
        return set(json.loads(last["message"])), last["time"]
    except Exception:
        return None


def save_shared(seen):
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{STATE_TOPIC}",
            data=json.dumps(sorted(seen)).encode("utf-8"))
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        print(f"  (목록 공유 실패: {e})")


def load_local():
    try:
        return set(json.load(open(os.path.join(HERE, "seen.json"), encoding="utf-8")))
    except Exception:
        return set()


def save_local(seen):
    try:
        json.dump(sorted(seen), open(os.path.join(HERE, "seen.json"), "w", encoding="utf-8"))
    except Exception:
        pass


# ---------- 알림 ----------
def phone(lines):
    if not PHONE_TOPIC:
        return
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{PHONE_TOPIC}",
            data=("\n".join(lines)).encode("utf-8"),
            headers={
                "Title": "Orange Dunes 2bu",
                "Priority": "high",
                "Tags": "golf",
                "Click": PAGE,
            })
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        print(f"  (폰 알림 실패: {e})")


def pc_alert(lines):
    import ctypes, winsound, webbrowser

    def _popup():
        for _ in range(3):
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            time.sleep(0.6)
        ctypes.windll.user32.MessageBoxW(
            0, "2부 자리가 생겼어요!\n\n" + "\n".join(lines),
            "오렌지듄스 2부 알림", 0x40 | 0x40000)
    threading.Thread(target=_popup, daemon=True).start()
    webbrowser.open(PAGE)


def log(msg):
    print(msg, flush=True)
    try:
        with open(os.path.join(HERE, "log.txt"), "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


# ---------- 한 번 확인 ----------
def check_once(on_pc):
    now = now_kst().strftime("%m-%d %H:%M:%S")
    found = find_2bu()
    shared = load_shared()
    if shared is not None:
        seen, last_time = shared
    else:
        seen, last_time = load_local(), 0
    new = sorted(v for k, v in found.items() if k not in seen)
    # 목록이 바뀌었거나 6시간 넘게 갱신 안 됐으면 공유 목록 저장
    if set(found) != seen or time.time() - last_time > 6 * 3600:
        save_shared(set(found))
    save_local(set(found))
    where = "PC" if on_pc else "서버"
    if new:
        log(f"[{now}] ({where}) ★ 2부 발견! " + ", ".join(new))
        phone(["2부 자리 생김!"] + new)
        if on_pc:
            pc_alert(new)
    else:
        cnt = f"(현재 2부 {len(found)}개)" if found else "(2부 없음)"
        log(f"[{now}] ({where}) 확인 완료 {cnt}")


def main():
    if "--once" in sys.argv:          # 서버용: 한 번만 확인하고 끝
        check_once(on_pc=False)
        return
    print("오렌지듄스 평일 2부 감시 시작 (끄려면 이 창을 닫으세요)")
    print(f"{CHECK_EVERY_SEC}초마다 확인합니다.\n")
    while True:
        try:
            check_once(on_pc=True)
        except Exception as e:
            log(f"[{now_kst().strftime('%m-%d %H:%M:%S')}] 확인 실패: {e} — 다음에 다시 시도")
        time.sleep(CHECK_EVERY_SEC)


if __name__ == "__main__":
    main()
