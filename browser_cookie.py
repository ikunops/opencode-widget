"""Browser cookie decryption (Chrome/Edge) for the Go usage widget.

仅作"尽力而为"的兜底：现代 Chrome (127+) 默认开启 app-bound encryption，
DPAPI 拿到的 key 无法解密 cookie（会返回空）。此模块在浏览器未启用
app-bound 加密（旧版 Chrome/Edge、或未开启 app-bound 的机器）时可用。

纯 Python AES-128/256 CBC/GCM 与 DPAPI 实现，避免依赖第三方库。
"""
import base64
import ctypes
import ctypes.wintypes as wt
import json
import os
import sqlite3
import tempfile

# ---------------------------------------------------------------- DPAPI
class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def dpapi_decrypt(data):
    b = _blob(data)
    out = _DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(b), None, None, None, None, 0, ctypes.byref(out))
    if not ok:
        raise OSError("CryptUnprotectData failed %d" % ctypes.GetLastError())
    return ctypes.string_at(out.pbData, out.cbData)


# ---------------------------------------------------------------- AES core
_SBOX = bytes([
    0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
    0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
    0xb7,0xfd,0x93,0x26,0x36,0x3f,0xf7,0xcc,0x34,0xa5,0xe5,0xf1,0x71,0xd8,0x31,0x15,
    0x04,0xc7,0x23,0xc3,0x18,0x96,0x05,0x9a,0x07,0x12,0x80,0xe2,0xeb,0x27,0xb2,0x75,
    0x09,0x83,0x2c,0x1a,0x1b,0x6e,0x5a,0xa0,0x52,0x3b,0xd6,0xb3,0x29,0xe3,0x2f,0x84,
    0x53,0xd1,0x00,0xed,0x20,0xfc,0xb1,0x5b,0x6a,0xcb,0xbe,0x39,0x4a,0x4c,0x58,0xcf,
    0xd0,0xef,0xaa,0xfb,0x43,0x4d,0x33,0x85,0x45,0xf9,0x02,0x7f,0x50,0x3c,0x9f,0xa8,
    0x51,0xa3,0x40,0x8f,0x92,0x9d,0x38,0xf5,0xbc,0xb6,0xda,0x21,0x10,0xff,0xf3,0xd2,
    0xcd,0x0c,0x13,0xec,0x5f,0x97,0x44,0x17,0xc4,0xa7,0x7e,0x3d,0x64,0x5d,0x19,0x73,
    0x60,0x81,0x4f,0xdc,0x22,0x2a,0x90,0x88,0x46,0xee,0xb8,0x14,0xde,0x5e,0x0b,0xdb,
    0xe0,0x32,0x3a,0x0a,0x49,0x06,0x24,0x5c,0xc2,0xd3,0xac,0x62,0x91,0x95,0xe4,0x79,
    0xe7,0xc8,0x37,0x6d,0x8d,0xd5,0x4e,0xa9,0x6c,0x56,0xf4,0xea,0x65,0x7a,0xae,0x08,
    0xba,0x78,0x25,0x2e,0x1c,0xa6,0xb4,0xc6,0xe8,0xdd,0x74,0x1f,0x4b,0xbd,0x8b,0x8a,
    0x70,0x3e,0xb5,0x66,0x48,0x03,0xf6,0x0e,0x61,0x35,0x57,0xb9,0x86,0xc1,0x1d,0x9e,
    0xe1,0xf8,0x98,0x11,0x69,0xd9,0x8e,0x94,0x9b,0x1e,0x87,0xe9,0xce,0x55,0x28,0xdf,
    0x8c,0xa1,0x89,0x0d,0xbf,0xe6,0x42,0x68,0x41,0x99,0x2d,0x0f,0xb0,0x54,0xbb,0x16])
_INV_SBOX = bytes(_SBOX.index(i) for i in range(256))
_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36]


def _xtime(a):
    return ((a << 1) & 0xff) ^ (0x1b if (a & 0x80) else 0)


def _mul(a, b):
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = _xtime(a)
        b >>= 1
    return r


def _load_state(blk):
    return [[blk[r + 4 * c] for c in range(4)] for r in range(4)]


def _store_state(st):
    return bytes(st[r][c] for c in range(4) for r in range(4))


def _expand_key(key):
    nk = len(key) // 4
    nr = nk + 6
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    for i in range(nk, 4 * (nr + 1)):
        t = w[i - 1][:]
        if i % nk == 0:
            t = t[1:] + t[:1]
            t = [_SBOX[b] for b in t]
            t[0] ^= _RCON[i // nk - 1]
        elif nk > 6 and i % nk == 4:
            t = [_SBOX[b] for b in t]
        w.append([w[i - nk][j] ^ t[j] for j in range(4)])
    rk = []
    for r in range(nr + 1):
        rk.append([w[4 * r][c] for c in range(4)] + [w[4 * r + 1][c] for c in range(4)] +
                  [w[4 * r + 2][c] for c in range(4)] + [w[4 * r + 3][c] for c in range(4)])
    return nr, rk


def _add_rk(st, rk):
    for r in range(4):
        for c in range(4):
            st[r][c] ^= rk[r + 4 * c]


def _aes_encrypt_block(blk, nr, rk):
    st = _load_state(blk)
    _add_rk(st, rk[0])
    for rnd in range(1, nr):
        for r in range(4):
            for c in range(4):
                st[r][c] = _SBOX[st[r][c]]
        for r in range(4):
            st[r] = st[r][r:] + st[r][:r]
        for c in range(4):
            col = [st[r][c] for r in range(4)]
            st[0][c] = _mul(col[0], 2) ^ _mul(col[1], 3) ^ col[2] ^ col[3]
            st[1][c] = col[0] ^ _mul(col[1], 2) ^ _mul(col[2], 3) ^ col[3]
            st[2][c] = col[0] ^ col[1] ^ _mul(col[2], 2) ^ _mul(col[3], 3)
            st[3][c] = _mul(col[0], 3) ^ col[1] ^ col[2] ^ _mul(col[3], 2)
        _add_rk(st, rk[rnd])
    for r in range(4):
        for c in range(4):
            st[r][c] = _SBOX[st[r][c]]
    for r in range(4):
        st[r] = st[r][r:] + st[r][:r]
    _add_rk(st, rk[nr])
    return _store_state(st)


def _aes_decrypt_block(blk, nr, rk):
    st = _load_state(blk)
    _add_rk(st, rk[nr])
    for rnd in range(nr - 1, 0, -1):
        for r in range(4):
            st[r] = st[r][-r:] + st[r][:-r]
        for r in range(4):
            for c in range(4):
                st[r][c] = _INV_SBOX[st[r][c]]
        _add_rk(st, rk[rnd])
        for c in range(4):
            col = [st[r][c] for r in range(4)]
            st[0][c] = _mul(col[0], 14) ^ _mul(col[1], 11) ^ _mul(col[2], 13) ^ _mul(col[3], 9)
            st[1][c] = _mul(col[0], 9) ^ _mul(col[1], 14) ^ _mul(col[2], 11) ^ _mul(col[3], 13)
            st[2][c] = _mul(col[0], 13) ^ _mul(col[1], 9) ^ _mul(col[2], 14) ^ _mul(col[3], 11)
            st[3][c] = _mul(col[0], 11) ^ _mul(col[1], 13) ^ _mul(col[2], 9) ^ _mul(col[3], 14)
    for r in range(4):
        st[r] = st[r][-r:] + st[r][:-r]
    for r in range(4):
        for c in range(4):
            st[r][c] = _INV_SBOX[st[r][c]]
    _add_rk(st, rk[0])
    return _store_state(st)


def _gf_mult(x, y):
    R = 0xe1000000000000000000000000000000
    z = 0
    v = y
    for i in range(127, -1, -1):
        if (x >> i) & 1:
            z ^= v
        v = ((v >> 1) ^ R) if (v & 1) else (v >> 1)
    return z


def _inc32(b):
    return b[:12] + ((int.from_bytes(b[12:], "big") + 1) & 0xffffffff).to_bytes(4, "big")


def _aes_gcm_decrypt(key, nonce, ct, tag):
    nr, rk = _expand_key(key)
    H = _aes_encrypt_block(bytes(16), nr, rk)
    Hn = int.from_bytes(H, "big")
    y = 0
    for i in range(0, len(ct), 16):
        y = _gf_mult(y ^ int.from_bytes(ct[i:i + 16].ljust(16, b"\x00"), "big"), Hn)
    y = _gf_mult(y ^ int.from_bytes((len(ct) * 8).to_bytes(8, "big"), "big"), Hn)
    S = y.to_bytes(16, "big")
    J0 = nonce + b"\x00\x00\x00\x01"
    E_J0 = _aes_encrypt_block(J0, nr, rk)
    if bytes(a ^ b for a, b in zip(E_J0, S)) != tag:
        raise ValueError("GCM tag mismatch")
    out = bytearray()
    counter = _inc32(J0)
    for i in range(0, len(ct), 16):
        ks = _aes_encrypt_block(counter, nr, rk)
        chunk = ct[i:i + 16]
        out += bytes(chunk[j] ^ ks[j] for j in range(len(chunk)))
        counter = _inc32(counter)
    return bytes(out)


def _aes_cbc_decrypt(key, iv, data):
    nr, rk = _expand_key(key)
    if len(data) % 16:
        raise ValueError("CBC len")
    prev = iv
    out = bytearray()
    for i in range(0, len(data), 16):
        blk = data[i:i + 16]
        d = _aes_decrypt_block(blk, nr, rk)
        out += bytes(d[j] ^ prev[j] for j in range(16))
        prev = blk
    return bytes(out)


def _decrypt_cookie_value(key, enc):
    """解密 Chrome/Edge cookie 的 encrypted_value。支持 v10 (CBC) / v20 (GCM)。"""
    if not enc or enc in (b"", b"v10", b"v20"):
        return None
    if enc.startswith(b"v20"):
        payload = enc[3:]
        if len(payload) < 28:
            return None
        nonce, tag, ct = payload[:12], payload[12:28], payload[28:]
        try:
            return _aes_gcm_decrypt(key, nonce, ct, tag)
        except Exception:
            return None
    if enc.startswith(b"v10"):
        payload = enc[3:]
        if len(payload) < 16 or len(payload) % 16:
            return None
        iv = payload[:16]
        return _aes_cbc_decrypt(key, iv, payload[16:])
    return None


# ---------------------------------------------------------------- 浏览器读取
def _local_state_path(browser):
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), browser, "User Data")
    return os.path.join(base, "Local State")


def _profile_cookies_path(browser, profile):
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), browser, "User Data")
    return os.path.join(base, profile, "Network", "Cookies")


def _browser_profiles(browser):
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), browser, "User Data")
    if not os.path.isdir(base):
        return []
    profiles = ["Default"]
    try:
        with open(os.path.join(base, "Local State"), "r", encoding="utf-8") as f:
            ls = json.load(f)
        info = ls.get("profile", {}).get("info_cache", {})
        names = [p for p in info.keys() if p not in ("Default",)]
    except Exception:
        names = []
    profiles.extend(names)
    return profiles


def _read_cookie(key, cookies_db):
    """从 cookie 库读取 opencode.ai 的 auth cookie。只读打开，锁定则跳过。"""
    if not os.path.exists(cookies_db):
        return ""
    tmp = os.path.join(tempfile.gettempdir(), "opencode", "cookie_%s.db" % abs(hash(cookies_db)))
    import shutil
    try:
        shutil.copy2(cookies_db, tmp)
        con = sqlite3.connect("file:%s?mode=ro" % tmp, uri=True)
    except Exception:
        return ""
    try:
        cur = con.cursor()
        rows = cur.execute(
            "SELECT host_key, name, encrypted_value FROM cookies "
            "WHERE name='auth' AND host_key LIKE '%opencode.ai%'").fetchall()
    except Exception:
        rows = []
    finally:
        con.close()
    for host, name, enc in rows:
        try:
            plain = _decrypt_cookie_value(key, enc)
        except Exception:
            plain = None
        if plain:
            try:
                return plain.decode("utf-8", errors="ignore")
            except Exception:
                return ""
    return ""


def read_auth_cookie_from_webdata():
    """尽力从本机 Chrome/Edge cookie 库读取 opencode.ai 的 auth cookie。

    现代 Chrome(127+) 默认 app-bound 加密，DPAPI key 解不开，返回空字符串。
    返回值为字符串 cookie（不带 'auth=' 前缀）；找不到返回 ''。
    """
    result = ""
    for browser in ("Google\\Chrome", "Microsoft\\Edge"):
        ls_path = _local_state_path(browser)
        if not os.path.exists(ls_path):
            continue
        try:
            with open(ls_path, "r", encoding="utf-8") as f:
                ls = json.load(f)
            oc = ls.get("os_crypt", {})
            # app-bound 加密启用时直接放弃（DPAPI key 无效）
            if oc.get("app_bound_encrypted_key"):
                continue
            ek = oc.get("encrypted_key") or ""
            if not ek:
                continue
            raw = base64.b64decode(ek)
            if raw[:5] != b"DPAPI":
                continue
            key = dpapi_decrypt(raw[5:])
        except Exception:
            continue
        for profile in _browser_profiles(browser):
            db = _profile_cookies_path(browser, profile)
            try:
                v = _read_cookie(key, db)
            except Exception:
                v = ""
            if v:
                return v
    return result