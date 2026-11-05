-- Menu: Library / File → Plug-in Extras → Editer
local LrFunctionContext = import 'LrFunctionContext'
local LrDialogs = import 'LrDialogs'
local LrTasks = import 'LrTasks'

LrTasks.startAsyncTask(function()
  LrFunctionContext.callWithContext('Editer', function(context)
    LrDialogs.attachErrorDialogToFunctionContext(context)
    require('EditerLR').huongDan()
  end)
end)
