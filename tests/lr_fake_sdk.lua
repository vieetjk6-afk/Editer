--[[ Lightroom Classic giả (đủ phần plugin Editer dùng) để chạy plugin ngoài Lightroom trong test.
Biến toàn cục do test đặt trước: FAKE_HOME (thư mục nhà), py_execute(cmd) -> mã thoát,
py_exists(path), py_mkdirs(path); WIN_ENV đặt lại sau khi nạp (true trên Windows, như Lightroom). ]]

local fake = { messages = {}, confirms = {}, writes = {}, active = nil, collections = {}, confirm_answer = 'other' }
FAKE = fake

-- Như Lightroom thật: KHÔNG có os.remove / os.rename / os.execute / os.exit / os.tmpname / io.popen
-- (lỗi thật đã gặp: "attempt to call field 'rename' (a nil value)")
os.remove, os.rename, os.execute, os.exit, os.tmpname = nil, nil, nil, nil, nil
io.popen = nil

local function deepcopy(v)
  if type(v) ~= 'table' then return v end
  local t = {}
  for k, x in pairs(v) do t[k] = deepcopy(x) end
  return t
end
fake.deepcopy = deepcopy

---------------------------------------------------------------- ảnh
local Photo = {}
Photo.__index = Photo

function fake.photo(id, path, settings, fmt)
  return setmetatable({ localIdentifier = id, _path = path, _settings = settings, _fmt = fmt or 'RAW',
                        ai_updates = 0 }, Photo)
end

function Photo:getRawMetadata(key)
  if key == 'path' then return self._path end
  if key == 'fileFormat' then return self._fmt end
  if key == 'isVirtualCopy' then return false end
  error('getRawMetadata: khoá chưa giả lập ' .. tostring(key))
end

function Photo:getDevelopSettings()
  return deepcopy(self._settings)
end

function Photo:applyDevelopSettings(s, historyName)
  assert(fake.inWrite, 'applyDevelopSettings phải nằm trong withWriteAccessDo')
  if fake.reject_masks and s.MaskGroupBasedCorrections and #s.MaskGroupBasedCorrections > 0 then
    error('mask không hợp lệ (giả lập Lightroom không nhận)')
  end
  for k, v in pairs(s) do self._settings[k] = deepcopy(v) end
  if self._settings.WhiteBalance == 'As Shot' and self.as_shot then    -- Lightroom tự điền As Shot của máy
    self._settings.Temperature = self.as_shot.temp
    self._settings.Tint = self.as_shot.tint
  end
  self.history = historyName
  self.applies = (self.applies or 0) + 1
end

function Photo:updateAISettings()
  assert(fake.inWrite, 'updateAISettings phải nằm trong withWriteAccessDo')
  self.ai_updates = self.ai_updates + 1
end

---------------------------------------------------------------- catalog
local Collection = {}
Collection.__index = Collection
-- Như Lightroom thật (lỗi thật 0.14.0): bộ sưu tập vừa tạo trong một withWriteAccessDo thì chính lần ghi đó không
-- đọc / sửa nó được
local function bornCheck(c)
  if fake.inWrite and c._born == fake.writeSeq then
    error("Can't get collection information after creating collection inside the same withWriteAccessDo function")
  end
end
function Collection:getPhotos() bornCheck(self) return { unpack(self._photos) } end
function Collection:removePhotos(ps)
  assert(fake.inWrite)
  bornCheck(self)
  local drop = {}
  for _, p in ipairs(ps) do drop[p] = true end
  local keep = {}
  for _, p in ipairs(self._photos) do if not drop[p] then keep[#keep + 1] = p end end
  self._photos = keep
end
function Collection:addPhotos(ps)
  assert(fake.inWrite)
  bornCheck(self)
  for _, p in ipairs(ps) do self._photos[#self._photos + 1] = p end
end

local catalog = { photos = {} }
fake.catalog = catalog
-- fake.selection: các ảnh đang chọn (mặc định: cả catalog)
function catalog:getMultipleSelectedOrAllPhotos() return { unpack(fake.selection or self.photos) } end
function catalog:getTargetPhotos() return { unpack(fake.selection or self.photos) } end
function catalog:getPath() return '/tmp/editer-test.lrcat' end
function catalog:withWriteAccessDo(name, fn, opts)
  assert(not fake.inWrite, 'withWriteAccessDo lồng nhau')
  fake.inWrite = true
  fake.writeSeq = (fake.writeSeq or 0) + 1
  fake.writes[#fake.writes + 1] = name
  local ok, err = pcall(fn)
  fake.inWrite = false
  if not ok then error(err) end
end
function catalog:createCollection(name, parent, canReturnPrior)
  assert(fake.inWrite, 'createCollection phải nằm trong withWriteAccessDo')
  if fake.collections[name] and canReturnPrior then return fake.collections[name] end
  local c = setmetatable({ name = name, _photos = {}, _born = fake.writeSeq }, Collection)
  fake.collections[name] = c
  return c
end
function catalog:setActiveSources(src) fake.active = src end
fake.added = {}
-- Lightroom trên Windows không phân biệt \ với / và chữ hoa/thường trong đường dẫn
local function samePath(a, b)
  local function norm(x)       -- chỉ hạ chữ ASCII: string.lower phụ thuộc locale, có thể làm hỏng UTF-8
    return (tostring(x):gsub('\\', '/'):gsub('[A-Z]', function(c) return string.char(c:byte() + 32) end))
  end
  return norm(a) == norm(b)
end
function catalog:findPhotoByPath(path)
  for _, p in ipairs(fake.added) do if samePath(p._path, path) then return p end end
  for _, p in ipairs(self.photos) do if samePath(p._path, path) then return p end end
  return nil
end
function catalog:addPhoto(path, stackWith, position)
  assert(fake.inWrite, 'addPhoto phải nằm trong withWriteAccessDo')
  assert(py_exists(path) == 'file', 'addPhoto: không có file ' .. tostring(path))
  assert(not self:findPhotoByPath(path), 'addPhoto: ảnh đã có trong catalog')     -- Lightroom báo lỗi như vậy
  local p = fake.photo(9000 + #fake.added, path, {}, 'JPG')
  fake.added[#fake.added + 1] = p
  return p
end

---------------------------------------------------------------- mô-đun Lr*
local Progress = {}
Progress.__index = Progress
function Progress:setCaption(c) self.caption = c end
function Progress:setPortionComplete() end
function Progress:setCancelable() end
function Progress:isCanceled() return false end
function Progress:done() self.finished = true end

local Session = {}
Session.__index = Session
function Session:doExportOnCurrentTask()
  local st = self.args.exportSettings
  assert(st.LR_format == 'JPEG' and st.LR_export_destinationType == 'specificFolder', 'xuất JPEG vào thư mục')
  assert(not st.LR_reimportExportedPhoto, 'không được đưa ảnh dựng thử vào catalog')
  self.paths = {}
  local used = {}
  for i, photo in ipairs(self.args.photosToExport) do
    local stem = (photo._path:match('[^/\\]*$')):gsub('%.[^.]*$', '')
    local name, k = stem .. '.jpg', 2
    while used[name] do name, k = stem .. '-' .. k .. '.jpg', k + 1 end    -- LR_collisionHandling = 'rename'
    used[name] = true
    local out = st.LR_export_destinationPathPrefix .. '/' .. name
    local size = st.LR_size_doConstrain == false and 0 or st.LR_size_maxWidth     -- 0: đầy đủ độ phân giải
    py_render(photo._path, require('EditerCore').encode(photo._settings), out, size)
    self.paths[i] = out
  end
  fake.exports = (fake.exports or 0) + 1
  fake.exported = (fake.exported or 0) + #self.args.photosToExport
end
function Session:renditions()
  local i = 0
  return function()
    i = i + 1
    if self.paths[i] then
      local path = self.paths[i]
      return i, { photo = self.args.photosToExport[i], waitForRender = function() return true, path end }
    end
  end
end

---------------------------------------------------------------- tác vụ chạy song song (như LrTasks)
local tasks = {}
local function inTask() return coroutine.running() ~= nil end
local function runTasks()
  for i = #tasks, 1, -1 do
    local co = tasks[i]
    if coroutine.status(co) == 'suspended' then
      local ok, err = coroutine.resume(co)
      if not ok then error(err) end
    end
    if coroutine.status(co) == 'dead' then table.remove(tasks, i) end
  end
end

local modules = {
  LrApplication = { activeCatalog = function() return catalog end },
  LrExportSession = setmetatable({}, { __call = function(_, args) return setmetatable({ args = args }, Session) end }),
  LrDialogs = {
    runOpenPanel = function(args) fake.open_panel = args return fake.open_panel_result end,
    message = function(title, msg, kind) fake.messages[#fake.messages + 1] = { title = title, msg = msg, kind = kind } end,
    confirm = function(msg, info) fake.confirms[#fake.confirms + 1] = msg .. ' ' .. (info or '') return fake.confirm_answer end,
    attachErrorDialogToFunctionContext = function() end,
    presentModalDialog = function(args) fake.modal = args return fake.modal_answer or 'ok' end,
  },
  LrView = {
    osFactory = function()
      return setmetatable({}, { __index = function(_, k)
        return function(_, t) local x = t or {} x._kind = k return x end
      end })
    end,
    bind = function(k) return { bind = k } end,
  },
  LrBinding = { makePropertyTable = function() return {} end },
  LrFileUtils = {
    exists = function(p) return py_exists(p) end,                 -- 'file' | 'directory' | false
    createAllDirectories = function(p) py_mkdirs(p) return true end,
    delete = function(p) return py_delete(p) end,
    move = function(a, b) return py_move(a, b) end,
    files = function(dir)
      local list, i = py_listdir(dir), 0
      return function() i = i + 1 return list[i] end
    end,
    directoryEntries = function(dir)
      local list, i = py_listdir(dir), 0
      return function() i = i + 1 return list[i] end
    end,
  },
  LrDate = { currentTime = function() return py_now() end },
  LrPathUtils = {
    child = function(a, b) return a .. '/' .. b end,
    parent = function(p) return (p:gsub('[/\\][^/\\]*$', '')) end,     -- như Lightroom: cả \ (Windows) lẫn /
    leafName = function(p) return (p:match('[^/\\]*$')) end,
    getStandardFilePath = function(k) assert(k == 'home', k) return FAKE_HOME end,
  },
  LrProgressScope = setmetatable({}, { __call = function(_, args) return setmetatable({ title = args.title }, Progress) end }),
  LrTasks = {
    pcall = function(f, ...) return pcall(f, ...) end,
    execute = function(cmd)
      fake.last_cmd = cmd
      if not inTask() then return py_execute(cmd) end
      local h = py_spawn(cmd)                -- chạy nền, tác vụ khác vẫn chạy trong lúc chờ
      while true do
        local code = py_poll(h)
        if code ~= nil then return code end
        coroutine.yield()
      end
    end,
    startAsyncTask = function(fn)
      local co = coroutine.create(fn)
      tasks[#tasks + 1] = co
      local ok, err = coroutine.resume(co)
      if not ok then error(err) end
    end,
    sleep = function(t)
      if inTask() then coroutine.yield() return end
      runTasks()
      py_sleep(t)
    end,
  },
  LrFunctionContext = { callWithContext = function(name, fn) return fn({}) end },
}

function import(name)
  local m = modules[name]
  if not m then error('import: chưa giả lập ' .. name) end
  return m
end

WIN_ENV = false
MAC_ENV = false
return fake
