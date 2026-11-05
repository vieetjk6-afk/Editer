--[[ EditerLR.lua — phần làm việc với Lightroom Classic: lấy ảnh + thông số trong catalog, gọi editer-anh.exe
phân tích (đọc file RAW, AI chia cảnh / tìm da), áp kết quả thẳng vào catalog (thấy ngay, Edit > Undo được). ]]

local LrApplication = import 'LrApplication'
local LrDate = import 'LrDate'
local LrDialogs = import 'LrDialogs'
local LrExportSession = import 'LrExportSession'
local LrFileUtils = import 'LrFileUtils'
local LrPathUtils = import 'LrPathUtils'
local LrProgressScope = import 'LrProgressScope'
local LrTasks = import 'LrTasks'

local Core = require 'EditerCore'

-- Lightroom: bắt lỗi khi gọi API của Lightroom phải dùng LrTasks.pcall (pcall thường không cho tác vụ "nhường")
local tpcall = LrTasks.pcall or pcall

local M = {}
local TITLE = 'Editer'
local COLLECTION = 'Editer – ảnh mẫu cần chỉnh'
local NOT_RAW = { VIDEO = true, JPG = true, JPEG = true, PSD = true, TIFF = true, PNG = true, HEIC = true,
                  AVIF = true }

---------------------------------------------------------------------------- file
local function dataDir()
  local d = LrPathUtils.child(LrPathUtils.getStandardFilePath('home'), 'Editer')
  if not LrFileUtils.exists(d) then LrFileUtils.createAllDirectories(d) end
  return d
end

local function readFile(path)
  local f = io.open(path, 'rb')
  if not f then return nil end
  local s = f:read('*a')
  f:close()
  return s
end

local function writeFile(path, s)
  local f = assert(io.open(path, 'wb'))
  f:write(s)
  f:close()
end

-- Lightroom không có os.remove / os.rename -> dùng LrFileUtils
local function deleteFile(path)
  if LrFileUtils.exists(path) then
    tpcall(function() LrFileUtils.delete(path) end)
  end
  return not LrFileUtils.exists(path)
end

--- Ghi file tạm rồi đổi tên, để editer-anh.exe không đọc phải file đang ghi dở.
local function writeAtomic(path, s)
  local tmp = path .. '.tmp'
  writeFile(tmp, s)
  deleteFile(path)
  tpcall(function() LrFileUtils.move(tmp, path) end)
  if not LrFileUtils.exists(path) then
    writeFile(path, s)
    deleteFile(tmp)
  end
end

local function newJobId()
  local t = 0
  tpcall(function() t = LrDate.currentTime() end)
  return string.format('%.0f-%d', t * 1000, math.random(100000, 999999))
end

local function logPath()
  return LrPathUtils.child(dataDir(), 'nhat-ky.txt')
end

---------------------------------------------------------------------------- trạng thái (ảnh tool đã đồng bộ, ảnh lúc bước 1)
local function catalogKey(catalog)
  local ok, p = tpcall(function() return catalog:getPath() end)
  return (ok and p) or 'catalog'
end

local function statePath()
  return LrPathUtils.child(dataDir(), 'lightroom-trang-thai.json')
end

local function loadState(catalog)
  local all = {}
  local s = readFile(statePath())
  if s then
    local ok, t = pcall(Core.decode, s)
    if ok and type(t) == 'table' then all = t end
  end
  local key = catalogKey(catalog)
  local st = all[key] or {}
  st.snap = st.snap or {}
  st.sync = st.sync or {}
  return st, all, key
end

local function saveState(all, key, st)
  all[key] = st
  writeFile(statePath(), Core.encode(all))
end

---------------------------------------------------------------------------- gọi editer-anh.exe
local function helperCmd()
  local s = readFile(LrPathUtils.child(dataDir(), 'lightroom.json'))
  if s then
    local ok, t = pcall(Core.decode, s)
    if ok and type(t) == 'table' and type(t.cmd) == 'table' and #t.cmd > 0 then return t.cmd end
  end
  local exe = LrPathUtils.child(LrPathUtils.parent(_PLUGIN.path), 'editer-anh.exe')
  if LrFileUtils.exists(exe) then return { exe } end
  return nil
end

local function runHelper(kind, job)
  local cmd = helperCmd()
  if not cmd then
    return nil, 'Chưa tìm thấy chương trình Editer.\nMở "Editer Anh.exe" → mục "Dùng trong Lightroom" → bấm '
      .. '"Cài vào Lightroom" (chỉ cần làm 1 lần), rồi chạy lại.'
  end
  local dir = LrPathUtils.child(dataDir(), 'lightroom')
  if not LrFileUtils.exists(dir) then LrFileUtils.createAllDirectories(dir) end
  local jobPath = LrPathUtils.child(dir, 'viec.json')
  local outPath = LrPathUtils.child(dir, 'ket-qua.json')
  job.viec_id = newJobId()
  writeFile(jobPath, Core.encode(job))
  deleteFile(outPath)
  local args = {}
  for _, a in ipairs(cmd) do args[#args + 1] = a end
  args[#args + 1] = kind
  args[#args + 1] = jobPath
  args[#args + 1] = outPath
  local status = LrTasks.execute(Core.commandLine(args, WIN_ENV))
  local s = readFile(outPath)
  if not s then
    return nil, 'Editer không chạy được (mã ' .. tostring(status) .. ').\nXem nhật ký: ' .. logPath()
  end
  local ok, res = pcall(Core.decode, s)
  if not ok or type(res) ~= 'table' then return nil, 'Kết quả Editer bị lỗi: ' .. tostring(res) end
  if res.viec_id ~= job.viec_id then
    return nil, 'Editer không chạy được (không có kết quả mới, mã ' .. tostring(status) .. ').\nXem nhật ký: ' .. logPath()
  end
  if not res.ok then return nil, tostring(res.loi or 'Editer báo lỗi') .. '\n\nNhật ký: ' .. logPath() end
  return res
end

---------------------------------------------------------------------------- ảnh trong catalog
local function targetPhotos(catalog)
  local photos
  local ok = tpcall(function() photos = catalog:getMultipleSelectedOrAllPhotos() end)
  if not ok or not photos or #photos == 0 then photos = catalog:getTargetPhotos() end
  return photos or {}
end

local function collect(photos, progress)
  local items, other = {}, 0
  for i, p in ipairs(photos) do
    local okf, fmt = tpcall(function() return p:getRawMetadata('fileFormat') end)
    if okf and fmt and NOT_RAW[string.upper(tostring(fmt))] then
      other = other + 1
    else
      local path = p:getRawMetadata('path')
      local s = p:getDevelopSettings() or {}
      items[#items + 1] = { photo = p, id = tostring(p.localIdentifier), path = path, settings = s,
                            fp = Core.fingerprint(s), name = LrPathUtils.leafName(path) }
    end
    if progress and i % 25 == 0 then progress:setPortionComplete(i, #photos) end
  end
  return items, other
end

local function hasMethod(obj, name)
  local ok, f = pcall(function() return obj[name] end)
  return ok and type(f) == 'function'
end

---------------------------------------------------------------------------- BƯỚC 1: chọn ảnh mẫu mỗi cảnh
function M.chonAnhMau()
  local catalog = LrApplication.activeCatalog()
  local photos = targetPhotos(catalog)
  if #photos < 2 then
    LrDialogs.message(TITLE, 'Mở thư mục job, chọn hết ảnh (Ctrl+A) rồi chạy lại.', 'info')
    return
  end
  local progress = LrProgressScope { title = 'Editer: chia cảnh' }
  progress:setCaption('Đọc thông số trong catalog...')
  local items, other = collect(photos, progress)
  if #items < 2 then
    progress:done()
    LrDialogs.message(TITLE, 'Không có ảnh RAW nào trong các ảnh đã chọn.', 'info')
    return
  end
  -- ảnh bạn ĐÃ chỉnh từ trước: giữ là ảnh mẫu (không bao giờ bị ghi đè)
  local st, all, key = loadState(catalog)
  local roles, baseline = Core.detectRoles(items, { snap = {}, sync = st.sync })
  local nmau = 0
  for i = 1, #items do if roles[i] == 'mau' then nmau = nmau + 1 end end
  if not baseline and nmau == #items and #items > 2 then
    progress:done()
    if LrDialogs.confirm('Editer: chưa phân biệt được ảnh nào bạn đã chỉnh',
        'Mọi ảnh đều có thông số khác nhau (có thể do preset Auto lúc import). Coi như bạn CHƯA chỉnh ảnh nào '
        .. 'trong các ảnh này? (Ảnh nào bạn chỉnh SAU bước này sẽ là ảnh mẫu.)',
        'Đúng, chưa chỉnh ảnh nào', 'Huỷ') ~= 'ok' then
      return
    end
    for i = 1, #items do roles[i] = 'dongbo' end
    progress = LrProgressScope { title = 'Editer: chia cảnh' }
  end
  -- ghi lại thông số hiện tại: ảnh nào bạn chỉnh sau bước này = ảnh mẫu
  for i, it in ipairs(items) do
    if roles[i] == 'mau' then st.snap[it.id] = nil else st.snap[it.id] = it.fp end
  end
  saveState(all, key, st)
  progress:setCaption(string.format('Editer đang chia cảnh %d ảnh...%s', #items,
    WIN_ENV and ' Cửa sổ đen là Editer đang chạy — đừng đóng.' or ''))
  progress:setPortionComplete(0, 1)
  local res, err = runHelper('lr-canh', Core.buildJob(items, nil, true))
  progress:done()
  if not res then
    LrDialogs.message(TITLE, err, 'critical')
    return
  end
  -- mỗi cảnh chưa có ảnh bạn chỉnh: 1 ảnh (giữa cảnh); cảnh nào cũng có rồi thì mở lại các ảnh mẫu để cân tiếp
  local reps, done, todo = Core.pickReps(res.canh or {}, roles)
  local list = #todo > 0 and todo or done
  local chosen = {}
  for _, i in ipairs(list) do chosen[#chosen + 1] = items[i].photo end
  local coll
  catalog:withWriteAccessDo('Editer: bộ sưu tập ảnh mẫu', function()
    coll = catalog:createCollection(COLLECTION, nil, true)
  end, { timeout = 30 })
  if coll then
    catalog:withWriteAccessDo('Editer: chọn ảnh mẫu mỗi cảnh', function()
      local old = coll:getPhotos()
      if old and #old > 0 then coll:removePhotos(old) end
      coll:addPhotos(chosen)
    end, { timeout = 30 })
    tpcall(function() catalog:setActiveSources({ coll }) end)
  end
  local head
  if #todo > 0 then
    head = string.format('Đã chia %d ảnh thành %d cảnh. %d cảnh chưa có ảnh bạn chỉnh: mỗi cảnh 1 ảnh đã vào '
      .. 'bộ sưu tập "%s".', #items, reps, #todo, COLLECTION)
    if #done > 0 then head = head .. string.format(' (%d cảnh đã có ảnh bạn chỉnh — bỏ qua.)', #done) end
    head = head .. '\n\nBây giờ: chỉnh từng ảnh đó trong Develop đến khi ưng (muốn chỉnh ảnh khác cùng cảnh '
      .. 'cũng được).'
  else
    head = string.format('Cảnh nào (%d cảnh) cũng đã có ảnh bạn chỉnh — các ảnh đó đã vào bộ sưu tập "%s" để '
      .. 'bạn cân tiếp nếu muốn.', reps, COLLECTION)
  end
  LrDialogs.message(TITLE, head .. '\n\nXong: quay lại thư mục job, chọn hết ảnh (Ctrl+A) → Library → '
    .. 'Plug-in Extras → "Editer: 2. Đồng bộ cả cảnh".'
    .. (other > 0 and string.format('\n\n(Bỏ qua %d ảnh không phải RAW.)', other) or ''), 'info')
end

---------------------------------------------------------------------------- BƯỚC 2: đồng bộ cả cảnh
local function updateAI(catalog, plan)
  local photos = {}
  for _, p in ipairs(plan) do
    if p.ai and hasMethod(p.item.photo, 'updateAISettings') then photos[#photos + 1] = p.item.photo end
  end
  if #photos == 0 then return 0 end
  local progress = LrProgressScope { title = 'Editer: tính lại mask AI' }
  tpcall(function() progress:setCancelable(true) end)
  local done = 0
  for i, photo in ipairs(photos) do
    if progress:isCanceled() then break end
    progress:setCaption(string.format('Tính lại mask AI %d/%d...', i, #photos))
    local ok = tpcall(function()
      catalog:withWriteAccessDo('Editer: tính lại mask AI', function() photo:updateAISettings() end,
        { timeout = 30 })
    end)
    if ok then done = done + 1 end
    progress:setPortionComplete(i, #photos)
  end
  progress:done()
  return done
end

function M.dongBo()
  local catalog = LrApplication.activeCatalog()
  local photos = targetPhotos(catalog)
  if #photos < 2 then
    LrDialogs.message(TITLE, 'Mở thư mục job, chọn hết ảnh (Ctrl+A) rồi chạy lại.', 'info')
    return
  end
  local progress = LrProgressScope { title = 'Editer: đồng bộ' }
  progress:setCaption('Đọc thông số trong catalog...')
  local items, other = collect(photos, progress)
  local st, all, key = loadState(catalog)
  local roles = Core.detectRoles(items, st)
  local nref, ntodo = 0, 0
  for i = 1, #items do
    if roles[i] == 'mau' then nref = nref + 1 else ntodo = ntodo + 1 end
  end
  if nref == 0 then
    progress:done()
    LrDialogs.message(TITLE, 'Chưa thấy ảnh nào bạn đã chỉnh trong các ảnh đã chọn.\n\n'
      .. 'Chạy "Editer: 1. Chọn ảnh mẫu mỗi cảnh", chỉnh các ảnh đó trong Develop, rồi chạy lại bước này.', 'info')
    return
  end
  if ntodo == 0 then
    progress:done()
    LrDialogs.message(TITLE, 'Mọi ảnh đã chọn đều đã được bạn chỉnh — không còn ảnh nào để đồng bộ.', 'info')
    return
  end
  progress:setCaption(string.format('Editer đang phân tích %d ảnh (%d ảnh bạn chỉnh)...%s', #items, nref,
    WIN_ENV and ' Cửa sổ đen là Editer đang chạy — đừng đóng.' or ''))
  progress:setPortionComplete(0, 1)
  local res, err = runHelper('lr-dong-bo', Core.buildJob(items, roles, true))
  if not res then
    progress:done()
    LrDialogs.message(TITLE, err, 'critical')
    return
  end
  local plan, notes = Core.makePlan(items, res)
  progress:setCaption(string.format('Áp thông số cho %d ảnh...', #plan))
  local failed = {}
  catalog:withWriteAccessDo('Editer: đồng bộ theo ảnh bạn chỉnh', function()
    for k, p in ipairs(plan) do
      local ok, e = tpcall(function() p.item.photo:applyDevelopSettings(p.settings, 'Editer: đồng bộ') end)
      if not ok then failed[#failed + 1] = p.item.name .. ': ' .. tostring(e) end
      if k % 10 == 0 then progress:setPortionComplete(k, #plan) end
    end
  end, { timeout = 60 })
  for _, p in ipairs(plan) do
    st.sync[p.item.id] = Core.fingerprint(p.item.photo:getDevelopSettings() or {})
  end
  saveState(all, key, st)
  progress:done()

  local nai = 0
  for _, p in ipairs(plan) do if p.ai then nai = nai + 1 end end
  local aiMsg = ''
  if nai > 0 then
    local canUpdate = false
    for _, p in ipairs(plan) do
      if p.ai and hasMethod(p.item.photo, 'updateAISettings') then canUpdate = true break end
    end
    if canUpdate and LrDialogs.confirm('Editer: tính lại mask AI?',
        string.format('%d ảnh mang mask AI (da, trời, chủ thể...) từ ảnh bạn chỉnh. Lightroom cần tính lại mask '
          .. 'trên từng ảnh (có thể mất vài phút).', nai), 'Tính ngay', 'Để sau') == 'ok' then
      local n = updateAI(catalog, plan)
      aiMsg = string.format('\nĐã tính lại mask AI cho %d ảnh.', n)
    else
      aiMsg = string.format('\n%d ảnh có mask AI cần Lightroom tính lại: chọn các ảnh đó → Develop → '
        .. 'Settings → Update AI Settings.', nai)
    end
  end
  local how = {}
  for k, n in pairs(notes.cach) do how[#how + 1] = string.format('%d ảnh theo %s', n, k) end
  table.sort(how)
  local msg = string.format('Đã đồng bộ %d ảnh theo %d ảnh bạn chỉnh.\n'
    .. 'Muốn hoàn tác: Edit → Undo "Editer: đồng bộ theo ảnh bạn chỉnh".\n\nĐộ sáng: %s.',
    #plan - #failed, res.so_mau or nref, #how > 0 and table.concat(how, ', ') or '—')
  if notes.khac_canh > 0 then
    msg = msg .. string.format('\n%d ảnh ở cảnh chưa có ảnh bạn chỉnh — theo ảnh bạn chỉnh giống nội dung nhất '
      .. '(chỉnh thêm 1 ảnh ở cảnh đó rồi chạy lại nếu chưa ưng).', notes.khac_canh)
  end
  if #notes.khong_mang > 0 then
    msg = msg .. '\n\nKhông mang sang (chỉ đúng vị trí trong ảnh mẫu): ' .. table.concat(notes.khong_mang, '; ')
  end
  msg = msg .. aiMsg
  if #notes.loi > 0 then
    msg = msg .. string.format('\n\nBỏ qua %d ảnh không đọc được file: %s', #notes.loi,
      Core.cut(table.concat(notes.loi, '; '), 400))
  end
  if #failed > 0 then
    msg = msg .. string.format('\n\nLightroom không nhận %d ảnh: %s', #failed, Core.cut(table.concat(failed, '; '), 400))
  end
  if other > 0 then msg = msg .. string.format('\n\n(Bỏ qua %d ảnh không phải RAW.)', other) end
  LrDialogs.message(TITLE, msg, 'info')
end

---------------------------------------------------------------------------- CHÉP MÀU TỪ ẢNH JPG MẪU
local function exportJpeg(photo, dir, size)
  local session = LrExportSession {
    photosToExport = { photo },
    exportSettings = {
      LR_export_destinationType = 'specificFolder',
      LR_export_destinationPathPrefix = dir,
      LR_export_useSubfolder = false,
      LR_format = 'JPEG',
      LR_jpeg_quality = 0.92,
      LR_export_colorSpace = 'sRGB',
      LR_size_doConstrain = true,
      LR_size_resizeType = 'wh',
      LR_size_maxWidth = size,
      LR_size_maxHeight = size,
      LR_size_units = 'pixels',
      LR_size_doNotEnlarge = true,
      LR_collisionHandling = 'overwrite',
      LR_renamingTokensOn = false,
      LR_outputSharpeningOn = false,
      LR_minimizeEmbeddedMetadata = true,
      LR_reimportExportedPhoto = false,
      LR_useWatermark = false,
    },
  }
  session:doExportOnCurrentTask()
  local path, err
  for _, rendition in session:renditions() do
    local ok, p = rendition:waitForRender()
    if ok then path = p else err = p end
  end
  return path, err
end

local function applyOne(catalog, photo, s, name)
  local ok, err = tpcall(function()
    catalog:withWriteAccessDo(name, function() photo:applyDevelopSettings(s, name) end, { timeout = 60 })
  end)
  return ok, err
end

-- Editer xin dựng thử 1 ảnh: áp thông số thử vào ảnh, (mask AI: nhờ Lightroom tính), xuất JPEG nhỏ.
local function serveRender(catalog, items, req, dir, touched, notes)
  local it = items[(req.i or -1) + 1]
  if not it then return { ok = false, loi = 'không thấy ảnh số ' .. tostring(req.i) } end
  touched[it.id] = it
  local s, hasMask = Core.copySettings(req.thong_so or {}, it.settings)
  local ok, err = applyOne(catalog, it.photo, s, 'Editer: thử màu')
  if not ok and hasMask then
    notes.mask_loi = tostring(err)
    hasMask = false
    ok, err = applyOne(catalog, it.photo, Core.withoutMasks(s), 'Editer: thử màu')
  end
  if not ok then return { ok = false, loi = 'Lightroom không nhận thông số: ' .. tostring(err) } end
  if hasMask and hasMethod(it.photo, 'updateAISettings') then
    tpcall(function()
      catalog:withWriteAccessDo('Editer: tính mask AI', function() it.photo:updateAISettings() end, { timeout = 60 })
    end)
  end
  local path, xerr = exportJpeg(it.photo, dir, req.co_anh or 1024)
  if not path then return { ok = false, loi = 'Lightroom không xuất được ảnh: ' .. tostring(xerr) } end
  return { ok = true, anh = path }
end

function M.chepMau()
  local catalog = LrApplication.activeCatalog()
  local photos = targetPhotos(catalog)
  if #photos < 1 then
    LrDialogs.message(TITLE, 'Chọn các ảnh cần chép màu (hoặc Ctrl+A cả thư mục) rồi chạy lại.', 'info')
    return
  end
  local jpgs = LrDialogs.runOpenPanel {
    title = 'Editer: chọn ảnh JPG mẫu (màu bạn muốn theo) — chọn được nhiều ảnh, mỗi cảnh 1 ảnh càng tốt',
    canChooseFiles = true, canChooseDirectories = false, allowsMultipleSelection = true,
    fileTypes = { 'jpg', 'jpeg', 'png', 'tif', 'tiff' },
  }
  if not jpgs or #jpgs == 0 then return end
  local progress = LrProgressScope { title = 'Editer: chép màu từ ảnh mẫu' }
  progress:setCaption('Đọc thông số trong catalog...')
  local items, other = collect(photos, progress)
  if #items == 0 then
    progress:done()
    LrDialogs.message(TITLE, 'Không có ảnh RAW nào trong các ảnh đã chọn.', 'info')
    return
  end
  local cmd = helperCmd()
  if not cmd then
    progress:done()
    LrDialogs.message(TITLE, 'Chưa tìm thấy chương trình Editer.\nMở "Editer Anh.exe" → mục "Dùng trong Lightroom" '
      .. '→ bấm "Cài vào Lightroom" (chỉ cần làm 1 lần), rồi chạy lại.', 'critical')
    return
  end
  local base = LrPathUtils.child(dataDir(), 'lightroom')
  local work = LrPathUtils.child(base, 'dung-thu')
  if not LrFileUtils.exists(work) then LrFileUtils.createAllDirectories(work) end
  local jobPath = LrPathUtils.child(base, 'viec-chep-mau.json')
  local outPath = LrPathUtils.child(base, 'ket-qua-chep-mau.json')
  local anh = {}
  for _, it in ipairs(items) do anh[#anh + 1] = { path = it.path } end
  local jobId = newJobId()
  writeFile(jobPath, Core.encode({ ai = true, anh = anh, mau_jpg = jpgs, lam_viec = work, co_anh = 1024,
                                   viec_id = jobId }))
  deleteFile(outPath)
  tpcall(function()                              -- dọn yêu cầu / kết quả dựng thử của lần trước
    local old = {}
    for f in LrFileUtils.files(work) do old[#old + 1] = f end
    for _, f in ipairs(old) do deleteFile(f) end
  end)
  local args = {}
  for _, a in ipairs(cmd) do args[#args + 1] = a end
  args[#args + 1] = 'lr-chep-mau'
  args[#args + 1] = jobPath
  args[#args + 1] = outPath
  local state = { done = false }
  LrTasks.startAsyncTask(function()
    state.code = LrTasks.execute(Core.commandLine(args, WIN_ENV))
    state.done = true
  end)
  progress:setCaption(string.format('Editer đang phân tích %d ảnh, Lightroom dựng thử ảnh đại diện để đo màu...%s',
    #items, WIN_ENV and ' Cửa sổ đen là Editer đang chạy — đừng đóng.' or ''))
  local touched, notes, n = {}, {}, 1
  local served, serveErr = tpcall(function()
    while true do
      local reqPath = LrPathUtils.child(work, 'yeu-cau-' .. n .. '.json')
      local s = readFile(reqPath)
      if s then
        local ok, req = pcall(Core.decode, s)
        local resp
        if ok and type(req) == 'table' and req.viec_id == jobId then
          local okr, r = tpcall(serveRender, catalog, items, req, work, touched, notes)
          resp = okr and r or { ok = false, loi = 'Lightroom lỗi khi dựng thử: ' .. tostring(r) }
        else
          resp = { ok = false, loi = 'yêu cầu dựng thử bị lỗi' }
        end
        writeAtomic(LrPathUtils.child(work, 'xong-' .. n .. '.json'), Core.encode(resp))
        progress:setCaption(string.format('Lightroom đã dựng thử %d ảnh...', n))
        n = n + 1
      elseif state.done then
        break
      else
        LrTasks.sleep(0.2)
      end
    end
  end)
  -- ảnh đại diện: LUÔN trả lại thông số gốc (kể cả khi có lỗi) trước khi áp kết quả
  for _, it in pairs(touched) do
    local orig = Core.deepcopy(it.settings)
    if orig.MaskGroupBasedCorrections == nil then orig.MaskGroupBasedCorrections = {} end
    applyOne(catalog, it.photo, orig, 'Editer: trả lại thông số gốc')
  end
  if not served then
    progress:done()
    LrDialogs.message(TITLE, 'Lỗi trong lúc Lightroom dựng thử: ' .. tostring(serveErr)
      .. '\nẢnh đại diện đã được trả lại thông số gốc.\nNhật ký: ' .. logPath(), 'critical')
    return
  end
  local out = readFile(outPath)
  local okd, res = false, nil
  if out then okd, res = pcall(Core.decode, out) end
  if okd and type(res) == 'table' and res.viec_id ~= jobId then
    okd, res = false, { loi = 'Editer không chạy được (không có kết quả mới, mã ' .. tostring(state.code) .. ').' }
  end
  if not okd or type(res) ~= 'table' or not res.ok then
    progress:done()
    LrDialogs.message(TITLE, (type(res) == 'table' and res.loi) and tostring(res.loi)
      or ('Editer không chạy được (mã ' .. tostring(state.code) .. ').'), 'critical')
    return
  end
  progress:setCaption('Áp màu cho cả lô...')
  local applied, failed, maskDropped, loi = 0, {}, 0, {}
  catalog:withWriteAccessDo('Editer: chép màu từ ảnh mẫu', function()
    for _, d in ipairs(res.anh or {}) do
      local it = items[(d.i or -1) + 1]
      if it and d.loi then
        loi[#loi + 1] = it.name .. ': ' .. tostring(d.loi)
      elseif it and d.thong_so then
        local s, hasMask = Core.copySettings(d.thong_so, it.settings)
        local ok, e = tpcall(function() it.photo:applyDevelopSettings(s, 'Editer: chép màu') end)
        if not ok and hasMask then
          maskDropped = maskDropped + 1
          ok, e = tpcall(function() it.photo:applyDevelopSettings(Core.withoutMasks(s), 'Editer: chép màu') end)
        end
        if ok then applied = applied + 1 else failed[#failed + 1] = it.name .. ': ' .. tostring(e) end
      end
    end
  end, { timeout = 60 })
  progress:done()
  local msg = string.format('Đã chép màu cho %d ảnh.\nMuốn hoàn tác: Edit → Undo "Editer: chép màu từ ảnh mẫu".\n\n'
    .. '%s %s\n(ΔE dưới 2: mắt gần như không phân biệt; 2-5: gần; trên 5: còn khác rõ)', applied,
    res.do_tren_lightroom and 'Đo trên ảnh Lightroom dựng:' or 'Ước lượng (chưa đo được trên Lightroom):',
    tostring(res.bao_cao or ''))
  if res.giu_goc then
    msg = msg .. '\n\nKhông tìm được thông số làm ảnh giống mẫu hơn ảnh gốc — đã giữ màu gốc cho nhóm đó.'
  end
  if maskDropped > 0 or notes.mask_loi then
    msg = msg .. '\n\nLightroom không nhận mask "Editer - Da/Nền" — chỉ chỉnh toàn ảnh.'
  end
  msg = msg .. '\n\nChưa ưng ảnh nào: chỉnh tay ảnh đó (hoặc 1 ảnh mỗi cảnh) rồi chạy "Editer: 2. Đồng bộ cả cảnh".'
  if #loi > 0 then
    msg = msg .. string.format('\n\nBỏ qua %d ảnh không đọc được file: %s', #loi, Core.cut(table.concat(loi, '; '), 300))
  end
  if #failed > 0 then
    msg = msg .. string.format('\n\nLightroom không nhận %d ảnh: %s', #failed, Core.cut(table.concat(failed, '; '), 300))
  end
  if other > 0 then msg = msg .. string.format('\n\n(Bỏ qua %d ảnh không phải RAW.)', other) end
  LrDialogs.message(TITLE, msg, 'info')
end

function M.huongDan()
  LrDialogs.message('Editer trong Lightroom Classic',
    'Bạn chỉnh tay 1 ảnh mỗi cảnh, Editer làm phần lặp lại cho cả cảnh — ngay trong catalog đang mở.\n\n'
    .. '1. Mở thư mục job, chọn hết ảnh (Ctrl+A) → Library → Plug-in Extras → "Editer: 1. Chọn ảnh mẫu mỗi cảnh".\n'
    .. '   Editer chia cảnh và đưa mỗi cảnh 1 ảnh vào bộ sưu tập "' .. COLLECTION .. '".\n'
    .. '2. Chỉnh từng ảnh đó trong Develop đến khi ưng (WB, tone, HSL, color grading, mask AI...).\n'
    .. '3. Quay lại thư mục job, Ctrl+A → "Editer: 2. Đồng bộ cả cảnh".\n\n'
    .. 'Mỗi ảnh lấy ĐÚNG thông số ảnh bạn chỉnh cùng cảnh; nhiệt độ màu giữ như ảnh mẫu (chỉ dịch khi ánh '
    .. 'sáng đổi thật); độ sáng cân theo da người + ISO / tốc độ / khẩu. Không mang sang: khung cắt, phối cảnh, '
    .. 'xoá vết, cọ tô, gradient. Ảnh bạn đã chỉnh không bị đụng tới. Chỉnh thêm rồi chạy lại bước 2 bao nhiêu '
    .. 'lần cũng được.\n\nCHÉP MÀU TỪ ẢNH JPG MẪU: chọn ảnh (Ctrl+A) → "Editer: Chép màu từ ảnh JPG mẫu…" → chọn 1 hoặc vài '
    .. 'ảnh JPG có màu bạn muốn. Lightroom dựng thử vài ảnh đại diện để Editer đo và sửa dần cho giống mẫu, rồi áp '
    .. 'cho cả lô. Cách chắc ăn nhất vẫn là: chép màu xong, chỉnh lại 1 ảnh mỗi cảnh cho ưng rồi chạy bước 2.'
    .. '\n\nNhật ký: ' .. logPath(), 'info')
end

return M
