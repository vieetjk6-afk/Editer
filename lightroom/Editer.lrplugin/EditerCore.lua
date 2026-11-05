--[[ EditerCore.lua — phần tính toán thuần của plugin (không gọi Lightroom) để test được ngoài Lightroom.
Viết cho Lua 5.1 (Lightroom Classic): không dùng goto, //, toán tử bit, thư viện utf8. ]]

local M = {}

---------------------------------------------------------------------------- JSON (trao đổi với editer-anh.exe)
local ESC = { ['"'] = '\\"', ['\\'] = '\\\\', ['\b'] = '\\b', ['\f'] = '\\f', ['\n'] = '\\n', ['\r'] = '\\r',
              ['\t'] = '\\t' }

local function isArray(t)
  local n = 0
  for k in pairs(t) do
    if type(k) ~= 'number' or k < 1 or math.floor(k) ~= k then return false, 0 end
    n = n + 1
  end
  for i = 1, n do
    if t[i] == nil then return false, 0 end
  end
  return true, n
end

local function enc(v, out)
  local t = type(v)
  if v == nil then
    out[#out + 1] = 'null'
  elseif t == 'boolean' then
    out[#out + 1] = v and 'true' or 'false'
  elseif t == 'number' then
    if v ~= v or v == math.huge or v == -math.huge then
      out[#out + 1] = 'null'
    elseif math.floor(v) == v and math.abs(v) < 1e15 then
      out[#out + 1] = string.format('%.0f', v)
    else
      out[#out + 1] = string.format('%.10g', v)
    end
  elseif t == 'string' then
    out[#out + 1] = '"' .. (v:gsub('[%c"\\]', function(c)
      return ESC[c] or string.format('\\u%04x', c:byte())
    end)) .. '"'
  elseif t == 'table' then
    local arr, n = isArray(v)
    if arr then
      out[#out + 1] = '['
      for i = 1, n do
        if i > 1 then out[#out + 1] = ',' end
        enc(v[i], out)
      end
      out[#out + 1] = ']'
    else
      local keys = {}
      for k in pairs(v) do keys[#keys + 1] = tostring(k) end
      table.sort(keys)
      out[#out + 1] = '{'
      for i, k in ipairs(keys) do
        if i > 1 then out[#out + 1] = ',' end
        enc(k, out)
        out[#out + 1] = ':'
        local x = v[k]
        if x == nil then x = v[tonumber(k)] end
        enc(x, out)
      end
      out[#out + 1] = '}'
    end
  else
    out[#out + 1] = 'null'
  end
end

function M.encode(v)
  local out = {}
  enc(v, out)
  return table.concat(out)
end

local function utf8char(cp)
  if cp < 0x80 then return string.char(cp) end
  if cp < 0x800 then return string.char(0xC0 + math.floor(cp / 0x40), 0x80 + cp % 0x40) end
  if cp < 0x10000 then
    return string.char(0xE0 + math.floor(cp / 0x1000), 0x80 + math.floor(cp / 0x40) % 0x40, 0x80 + cp % 0x40)
  end
  return string.char(0xF0 + math.floor(cp / 0x40000), 0x80 + math.floor(cp / 0x1000) % 0x40,
                     0x80 + math.floor(cp / 0x40) % 0x40, 0x80 + cp % 0x40)
end

local UNESC = { ['"'] = '"', ['\\'] = '\\', ['/'] = '/', b = '\b', f = '\f', n = '\n', r = '\r', t = '\t' }

local function skipWs(s, i)
  return s:find('[^ \t\r\n]', i) or (#s + 1)
end

local parse

local function parseString(s, i)          -- s:sub(i, i) == '"'
  local buf, j = {}, i + 1
  while true do
    local k = s:find('["\\]', j)
    if not k then error('JSON: thiếu dấu " đóng chuỗi') end
    buf[#buf + 1] = s:sub(j, k - 1)
    if s:sub(k, k) == '"' then return table.concat(buf), k + 1 end
    local e = s:sub(k + 1, k + 1)
    if UNESC[e] then
      buf[#buf + 1] = UNESC[e]
      j = k + 2
    elseif e == 'u' then
      local cp = tonumber(s:sub(k + 2, k + 5), 16) or 63
      j = k + 6
      if cp >= 0xD800 and cp <= 0xDBFF and s:sub(j, j + 1) == '\\u' then
        local lo = tonumber(s:sub(j + 2, j + 5), 16)
        if lo and lo >= 0xDC00 and lo <= 0xDFFF then
          cp = 0x10000 + (cp - 0xD800) * 0x400 + (lo - 0xDC00)
          j = j + 6
        end
      end
      buf[#buf + 1] = utf8char(cp)
    else
      error('JSON: ký tự thoát lạ \\' .. e)
    end
  end
end

parse = function(s, i)
  i = skipWs(s, i)
  local c = s:sub(i, i)
  if c == '{' then
    local t = {}
    i = skipWs(s, i + 1)
    if s:sub(i, i) == '}' then return t, i + 1 end
    while true do
      if s:sub(i, i) ~= '"' then error('JSON: cần tên khoá ở vị trí ' .. i) end
      local k
      k, i = parseString(s, i)
      i = skipWs(s, i)
      if s:sub(i, i) ~= ':' then error('JSON: thiếu dấu : ở vị trí ' .. i) end
      local v
      v, i = parse(s, i + 1)
      t[k] = v
      i = skipWs(s, i)
      local d = s:sub(i, i)
      if d == '}' then return t, i + 1 end
      if d ~= ',' then error('JSON: thiếu dấu , ở vị trí ' .. i) end
      i = skipWs(s, i + 1)
    end
  elseif c == '[' then
    local t, n = {}, 0
    i = skipWs(s, i + 1)
    if s:sub(i, i) == ']' then return t, i + 1 end
    while true do
      local v
      v, i = parse(s, i)
      n = n + 1
      t[n] = v
      i = skipWs(s, i)
      local d = s:sub(i, i)
      if d == ']' then return t, i + 1 end
      if d ~= ',' then error('JSON: thiếu dấu , trong mảng ở vị trí ' .. i) end
      i = i + 1
    end
  elseif c == '"' then
    return parseString(s, i)
  elseif s:sub(i, i + 3) == 'true' then
    return true, i + 4
  elseif s:sub(i, i + 4) == 'false' then
    return false, i + 5
  elseif s:sub(i, i + 3) == 'null' then
    return nil, i + 4
  else
    local a, b = s:find('^-?%d+%.?%d*[eE]?[-+]?%d*', i)
    if not a then error('JSON: giá trị lạ ở vị trí ' .. i) end
    return tonumber(s:sub(a, b)), b + 1
  end
end

function M.decode(s)
  local v = parse(s, 1)
  return v
end

---------------------------------------------------------------------------- dấu vân tay thông số
local function startsWithAny(k, list)
  for _, p in ipairs(list) do
    if k:sub(1, #p) == p then return true end
  end
  return false
end

-- Bỏ khỏi dấu vân tay: thứ khác nhau giữa các ảnh dù bạn không chỉnh (khung cắt, xoay, ống kính, mã kiểm tra)
local FP_SKIP = { 'Crop', 'HasCrop', 'Orientation', 'Upright', 'Perspective', 'RawFileName', 'AlreadyApplied',
                  'HasSettings', 'LensProfile' }

local function fpSkip(k)
  return type(k) == 'string' and (startsWithAny(k, FP_SKIP) or k:find('Digest', 1, true) ~= nil)
end

local function ser(v, out)
  local t = type(v)
  if t == 'number' then
    if math.abs(v) < 0.0005 then out[#out + 1] = '0' else out[#out + 1] = string.format('%.3f', v) end
  elseif t == 'string' then
    out[#out + 1] = string.format('%q', v)
  elseif t == 'boolean' then
    out[#out + 1] = v and 'T' or 'F'
  elseif t == 'table' then
    local keys = {}
    for k in pairs(v) do
      if not fpSkip(k) then keys[#keys + 1] = k end
    end
    table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
    out[#out + 1] = '{'
    for _, k in ipairs(keys) do
      out[#out + 1] = tostring(k)
      out[#out + 1] = '='
      ser(v[k], out)
      out[#out + 1] = ';'
    end
    out[#out + 1] = '}'
  end
end

function M.hash(s)            -- 2 hàm băm đa thức, mỗi vòng 4 byte (số thực Lua vẫn chính xác: < 2^53)
  local h1, h2 = 0, 0
  for i = 1, #s, 4 do
    local a, b, c, d = s:byte(i, i + 3)
    local v = a + (b or 0) * 256 + (c or 0) * 65536 + (d or 0) * 16777216
    h1 = (h1 * 65599 + v) % 2147483629
    h2 = (h2 * 65587 + v) % 2147483587
  end
  return string.format('%08x%08x', h1, h2)
end

--- Dấu vân tay thông số chỉnh màu của 1 ảnh (bỏ khung cắt, ống kính; As Shot thì bỏ số nhiệt độ của máy).
function M.fingerprint(settings)
  local s = {}
  local asShot = settings.WhiteBalance == 'As Shot'
  for k, v in pairs(settings) do
    if not (asShot and (k == 'Temperature' or k == 'Tint')) then s[k] = v end
  end
  local out = {}
  ser(s, out)
  return M.hash(table.concat(out))
end

---------------------------------------------------------------------------- ảnh có chỉnh thật không
local EDIT_EXACT = {}
for _, k in ipairs({ 'Exposure2012', 'Contrast2012', 'Highlights2012', 'Shadows2012', 'Whites2012', 'Blacks2012',
                     'Texture', 'Clarity2012', 'Dehaze', 'Vibrance', 'Saturation', 'ParametricShadows',
                     'ParametricDarks', 'ParametricLights', 'ParametricHighlights', 'GrainAmount',
                     'PostCropVignetteAmount', 'RedHue', 'RedSaturation', 'GreenHue', 'GreenSaturation', 'BlueHue',
                     'BlueSaturation', 'ShadowTint' }) do
  EDIT_EXACT[k] = true
end

local function isEditKey(k)
  if EDIT_EXACT[k] then return true end
  if k:find('^HueAdjustment') or k:find('^SaturationAdjustment') or k:find('^LuminanceAdjustment') then
    return true
  end
  if k:find('^SplitToning') and k:find('Saturation$') then return true end
  return k:find('^ColorGrade') ~= nil and (k:find('Sat$') ~= nil or k:find('Lum$') ~= nil)
end

--- Ảnh có chỉnh gì thật không (toàn mặc định -> không phải ảnh mẫu).
function M.hasEdits(s)
  if s.WhiteBalance and s.WhiteBalance ~= 'As Shot' then return true end
  for k, v in pairs(s) do
    if type(k) == 'string' and type(v) == 'number' and math.abs(v) > 1e-6 and isEditKey(k) then return true end
  end
  local curve = s.ToneCurveName2012
  if type(curve) == 'string' and curve ~= '' and curve ~= 'Linear' then return true end
  local prof = s.CameraProfile
  if type(prof) == 'string' and prof ~= '' and prof ~= 'Adobe Standard' and prof ~= 'Embedded' then return true end
  local look = s.Look
  local name = type(look) == 'table' and look.Name or look
  if type(name) == 'string' and name ~= '' and name ~= 'Adobe Color' then return true end
  local m = s.MaskGroupBasedCorrections
  return type(m) == 'table' and next(m) ~= nil
end

---------------------------------------------------------------------------- ảnh nào bạn đã chỉnh (ảnh mẫu)
--[[ items[i] = { id, fp, settings }; state = { snap = {id -> fp lúc chạy bước 1}, sync = {id -> fp lúc tool đồng bộ} }
-> roles[i] = 'mau' | 'dongbo', cách nhận ra.
  - tool đã đồng bộ và bạn chưa đụng lại -> đồng bộ lại được;
  - có chụp lại ở bước 1: thông số đổi từ lúc đó = bạn đã chỉnh;
  - không có: so với "nền" chung của lô (ảnh chưa chỉnh / preset lúc import giống hệt nhau). ]]
function M.detectRoles(items, state)
  local roles, unknown = {}, {}
  local snap, sync = state.snap or {}, state.sync or {}
  for i, it in ipairs(items) do
    if sync[it.id] and sync[it.id] == it.fp then
      roles[i] = 'dongbo'
    elseif snap[it.id] then
      roles[i] = (snap[it.id] ~= it.fp) and 'mau' or 'dongbo'
    else
      unknown[#unknown + 1] = i
    end
  end
  local baseline
  if #unknown > 0 then
    local count = {}
    for _, i in ipairs(unknown) do
      local fp = items[i].fp
      count[fp] = (count[fp] or 0) + 1
    end
    local best, n = nil, 0
    for fp, c in pairs(count) do
      if c > n or (c == n and best ~= nil and fp < best) then best, n = fp, c end
    end
    if n >= 2 and n >= 0.25 * #unknown then baseline = best end
    for _, i in ipairs(unknown) do
      local it = items[i]
      local edited = M.hasEdits(it.settings)
      if baseline then edited = edited and it.fp ~= baseline end
      roles[i] = edited and 'mau' or 'dongbo'
    end
  end
  return roles, baseline
end

---------------------------------------------------------------------------- thông số mang sang ảnh khác
local DROP_PREFIX = { 'Crop', 'HasCrop', 'Orientation', 'Upright', 'RawFileName', 'AlreadyApplied', 'HasSettings',
                      'PerspectiveVertical', 'PerspectiveHorizontal', 'PerspectiveRotate', 'PerspectiveScale',
                      'PerspectiveAspect', 'PerspectiveX', 'PerspectiveY' }
local DROP_KEY = { RetouchInfo = true, RetouchAreas = true, RedEyeInfo = true, PaintBasedCorrections = true,
                   GradientBasedCorrections = true, CircularGradientBasedCorrections = true }
local LENS_ID = { LensProfileName = true, LensProfileFilename = true, LensProfileDigest = true,
                  LensProfileIsEmbedded = true }
local MASK_KEEP = { ['Mask/Image'] = true, ['Mask/RangeMask'] = true }
local MASK_NAME = { ['Mask/Paint'] = 'cọ tô tay', ['Mask/Gradient'] = 'gradient thẳng',
                    ['Mask/CircularGradient'] = 'gradient tròn' }
local DIGEST = { InputDigest = true, InputDigestVersion = true, LocalInputDigest = true,
                 LocalInputDigestVersion = true, ErrorReason = true }

local function deepcopy(v, strip)
  if type(v) ~= 'table' then return v end
  local t = {}
  for k, x in pairs(v) do
    if not (strip and DIGEST[k]) then t[k] = deepcopy(x, strip) end
  end
  return t
end
M.deepcopy = deepcopy

local function maskKinds(t, acc)
  for k, v in pairs(t) do
    if k == 'What' and type(v) == 'string' and v:sub(1, 5) == 'Mask/' then
      acc[v] = true
    elseif type(v) == 'table' then
      maskKinds(v, acc)
    end
  end
  return acc
end

--- Thông số ảnh mẫu -> phần mang sang ảnh khác cùng cảnh (giống "Sync" của Lightroom, bỏ thứ chỉ đúng cho
--- riêng tấm mẫu). o = { sameCamera, sameLens, asShot } -> settings, các mask không mang sang, số mask AI.
function M.forSync(ref, o)
  local s, skipped, ai = {}, {}, 0
  for k, v in pairs(ref) do
    local drop = DROP_KEY[k] or (type(k) == 'string' and startsWithAny(k, DROP_PREFIX))
      or (k == 'PerspectiveUpright' and v == 5)
      or (not o.sameLens and LENS_ID[k])
      or (not o.sameCamera and k == 'CameraProfileDigest')
      or (o.asShot and (k == 'Temperature' or k == 'Tint'))
    if not drop then s[k] = deepcopy(v, k == 'MaskGroupBasedCorrections') end
  end
  if not o.sameLens and s.LensProfileSetup == 'Custom' then s.LensProfileSetup = 'LensDefaults' end
  local groups = s.MaskGroupBasedCorrections
  if type(groups) == 'table' then
    local keep, arr, n = {}, isArray(groups)
    if not arr and next(groups) ~= nil then
      skipped[#skipped + 1] = 'mask (không rõ cấu trúc)'
    end
    for i = 1, (arr and n or 0) do
      local corr = groups[i]
      local kinds = type(corr) == 'table' and maskKinds(corr, {}) or {}
      local ok, bad = next(kinds) ~= nil, {}
      for kind in pairs(kinds) do
        if not MASK_KEEP[kind] then
          ok = false
          bad[#bad + 1] = MASK_NAME[kind] or (kind:gsub('^Mask/', ''))
        end
      end
      if ok then
        keep[#keep + 1] = corr
        if kinds['Mask/Image'] then ai = ai + 1 end
      else
        table.sort(bad)
        local name = type(corr) == 'table' and corr.CorrectionName or nil
        skipped[#skipped + 1] = (name or 'mask') .. ' (' .. (#bad > 0 and table.concat(bad, ', ') or 'không rõ loại')
          .. ')'
      end
    end
    s.MaskGroupBasedCorrections = keep
  end
  return s, skipped, ai
end

local function round(x, step)
  return math.floor(x / step + 0.5) * step
end

--- Thông số áp cho 1 ảnh: phần mang sang từ ảnh mẫu + Exposure / WB riêng do Editer tính (d).
function M.settingsFor(ref, d)
  local s, skipped, ai = M.forSync(ref, { sameCamera = d.cung_may, sameLens = d.cung_ong_kinh, asShot = d.as_shot })
  s.Exposure2012 = round(d.Exposure2012, 0.01)
  if not d.as_shot then
    s.WhiteBalance = 'Custom'
    s.Temperature = round(d.Temperature, 1)
    s.Tint = round(d.Tint, 1)
  end
  return s, skipped, ai
end

---------------------------------------------------------------------------- trao đổi với editer-anh.exe
--- Danh sách ảnh gửi đi: đường dẫn, ảnh nào là mẫu (+ Exposure / WB của ảnh mẫu trong catalog).
function M.buildJob(items, roles, useAi)
  local anh = {}
  for i, it in ipairs(items) do
    local e = { path = it.path, mau = roles and roles[i] == 'mau' or false }
    if e.mau then
      local s = it.settings
      e.thong_so = { Exposure2012 = s.Exposure2012 or 0, WhiteBalance = s.WhiteBalance or 'As Shot',
                     Temperature = s.Temperature, Tint = s.Tint }
    end
    anh[#anh + 1] = e
  end
  return { ai = useAi ~= false, anh = anh }
end

--- Kết quả của editer-anh.exe -> việc cần làm: { {item, settings, decision}... } + ghi chú.
function M.makePlan(items, res)
  local plan, notes = {}, { loi = {}, khong_mang = {}, ai = 0, cach = {}, khac_canh = 0 }
  local seen = {}
  for _, d in ipairs(res.anh or {}) do
    local it = items[(d.i or -1) + 1]
    if it and d.loi then
      notes.loi[#notes.loi + 1] = (it.name or it.path) .. ': ' .. tostring(d.loi)
    elseif it and d.r then
      local ref = items[d.r + 1]
      local s, skipped, ai = M.settingsFor(ref.settings, d)
      plan[#plan + 1] = { item = it, settings = s, d = d, ref = ref, ai = ai > 0 }
      if ai > 0 then notes.ai = notes.ai + 1 end
      if #skipped > 0 and not seen[d.r] then
        seen[d.r] = true
        notes.khong_mang[#notes.khong_mang + 1] = (ref.name or ref.path) .. ': ' .. table.concat(skipped, ', ')
      end
      notes.cach[d.cach or '?'] = (notes.cach[d.cach or '?'] or 0) + 1
      if d.cung_canh == false then notes.khac_canh = notes.khac_canh + 1 end
    end
  end
  return plan, notes
end

--- Cảnh (kết quả lr-canh, chỉ số từ 0) -> số cảnh, ảnh mẫu đã có (1 ảnh mỗi cảnh, gần giữa cảnh nhất),
--- ảnh cần bạn chỉnh (ảnh giữa của mỗi cảnh chưa có ảnh mẫu). Chỉ số trả về từ 1.
function M.pickReps(scenes, roles)
  local done, todo = {}, {}
  for _, sc in ipairs(scenes) do
    local idx = sc.anh or {}
    local mid = (sc.mau or idx[1] or 0) + 1
    local pos = {}
    for k, i in ipairs(idx) do pos[i + 1] = k end
    local best, dist = nil, nil
    for _, i in ipairs(idx) do
      if roles[i + 1] == 'mau' then
        local dd = math.abs((pos[i + 1] or 0) - (pos[mid] or 0))
        if not best or dd < dist then best, dist = i + 1, dd end
      end
    end
    if best then done[#done + 1] = best else todo[#todo + 1] = mid end
  end
  return #scenes, done, todo
end

--- Thông số chép màu (từ editer-anh.exe) -> bảng áp vào ảnh. Mask "Editer - ..." mới thay mask Editer cũ,
--- mask khác của bạn giữ nguyên.
function M.copySettings(thongSo, current)
  local s, editer = {}, nil
  for k, v in pairs(thongSo) do
    if k == 'Editer_mask' then editer = v else s[k] = deepcopy(v) end
  end
  local groups = current and current.MaskGroupBasedCorrections
  local hadEditer = false
  local keep = {}
  if type(groups) == 'table' then
    for _, corr in ipairs(groups) do
      local name = type(corr) == 'table' and corr.CorrectionName or nil
      if type(name) == 'string' and name:sub(1, 9) == 'Editer - ' then
        hadEditer = true
      else
        keep[#keep + 1] = deepcopy(corr)
      end
    end
  end
  if editer and #editer > 0 then
    for _, corr in ipairs(editer) do keep[#keep + 1] = deepcopy(corr) end
    s.MaskGroupBasedCorrections = keep
    s.EnableMaskGroupBasedCorrections = true
  elseif hadEditer then
    s.MaskGroupBasedCorrections = keep
  end
  return s, editer ~= nil and #editer > 0
end

--- Bỏ mask khỏi bảng thông số (khi Lightroom không nhận mask).
function M.withoutMasks(s, current)
  local t = {}
  for k, v in pairs(s) do
    if k ~= 'MaskGroupBasedCorrections' and k ~= 'EnableMaskGroupBasedCorrections' then t[k] = v end
  end
  return t
end

--- Dòng lệnh chạy chương trình (Windows: cmd.exe bỏ cặp ngoặc kép ngoài cùng -> phải bọc thêm 1 lớp).
function M.commandLine(args, windows)
  local q = {}
  for _, a in ipairs(args) do q[#q + 1] = '"' .. tostring(a) .. '"' end
  local line = table.concat(q, ' ')
  if windows then line = '"' .. line .. '"' end
  return line
end

return M
