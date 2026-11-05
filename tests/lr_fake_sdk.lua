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
  for k, v in pairs(s) do self._settings[k] = deepcopy(v) end
  self.history = historyName
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

local modules = {
  LrApplication = { activeCatalog = function() return catalog end },
  LrDialogs = {
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
    execute = function(cmd) fake.last_cmd = cmd return py_execute(cmd) end,
    startAsyncTask = function(fn) fn() end,
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
