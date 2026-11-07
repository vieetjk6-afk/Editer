-- Menu: Library / File → Plug-in Extras → Editer: Chép màu kiểu Evoto (ảnh mới / preset Camera Raw)
local LrFunctionContext = import 'LrFunctionContext'
local LrDialogs = import 'LrDialogs'
local LrTasks = import 'LrTasks'

LrTasks.startAsyncTask(function()
  LrFunctionContext.callWithContext('Editer', function(context)
    LrDialogs.attachErrorDialogToFunctionContext(context)
    require('EditerLR').chepMauAnh()
  end)
end)
