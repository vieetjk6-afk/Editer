--[[ Lightroom Classic giả (đủ phần plugin Editer dùng) để chạy plugin ngoài Lightroom trong test.
Biến toàn cục do test đặt trước: FAKE_HOME (thư mục nhà), py_execute(cmd) -> mã thoát,
py_exists(path), py_mkdirs(path); WIN_ENV đặt lại sau khi nạp (true trên Windows, như Lightroom). ]]

local fake = { messages = {}, confirms = {}, writes = {}, active = nil, collections = {}, confirm_answer = 'other' }
FAKE = fake

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
function Collection:getPhotos() return { unpack(self._photos) } end
function Collection:removePhotos(ps)
  assert(fake.inWrite)
  local drop = {}
  for _, p in ipairs(ps) do drop[p] = true end
  local keep = {}
  for _, p in ipairs(self._photos) do if not drop[p] then keep[#keep + 1] = p end end
  self._photos = keep
end
function Collection:addPhotos(ps)
  assert(fake.inWrite)
  for _, p in ipairs(ps) do self._photos[#self._photos + 1] = p end
end

local catalog = { photos = {} }
fake.catalog = catalog
function catalog:getMultipleSelectedOrAllPhotos() return { unpack(self.photos) } end
function catalog:getTargetPhotos() return { unpack(self.photos) } end
function catalog:getPath() return '/tmp/editer-test.lrcat' end
function catalog:withWriteAccessDo(name, fn, opts)
  assert(not fake.inWrite, 'withWriteAccessDo lồng nhau')
  fake.inWrite = true
  fake.writes[#fake.writes + 1] = name
  local ok, err = pcall(fn)
  fake.inWrite = false
  if not ok then error(err) end
end
function catalog:createCollection(name, parent, canReturnPrior)
  assert(fake.inWrite, 'createCollection phải nằm trong withWriteAccessDo')
  if fake.collections[name] and canReturnPrior then return fake.collections[name] end
  local c = setmetatable({ name = name, _photos = {} }, Collection)
  fake.collections[name] = c
  return c
end
function catalog:setActiveSources(src) fake.active = src end

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
  for i, photo in ipairs(self.args.photosToExport) do
    local name = (photo._path:match('[^/\\]*$')):gsub('%.[^.]*$', '') .. '.jpg'
    local out = st.LR_export_destinationPathPrefix .. '/' .. name
    py_render(photo._path, require('EditerCore').encode(photo._settings), out, st.LR_size_maxWidth)
    self.paths[i] = out
  end
  fake.exports = (fake.exports or 0) + 1
end
function Session:renditions()
  local i = 0
  return function()
    i = i + 1
    if self.paths[i] then
      local path = self.paths[i]
      return i, { waitForRender = function() return true, path end }
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
  },
  LrFileUtils = {
    exists = function(p) return py_exists(p) end,                 -- 'file' | 'directory' | false
    createAllDirectories = function(p) py_mkdirs(p) return true end,
  },
  LrPathUtils = {
    child = function(a, b) return a .. '/' .. b end,
    parent = function(p) return (p:gsub('/[^/]*$', '')) end,
    leafName = function(p) return (p:match('[^/\\]*$')) end,
    getStandardFilePath = function(k) assert(k == 'home', k) return FAKE_HOME end,
  },
  LrProgressScope = setmetatable({}, { __call = function(_, args) return setmetatable({ title = args.title }, Progress) end }),
  LrTasks = {
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
