--[[ Editer Ảnh cho Lightroom Classic.
Bạn chỉnh tay 1 ảnh mỗi cảnh, Editer áp đúng thông số đó cho cả cảnh ngay trong catalog đang mở
(chỉ tự cân lại nhiệt độ màu + độ sáng từng ảnh). Phần phân tích ảnh do editer-anh.exe làm. ]]
return {
  LrSdkVersion = 13.0,
  LrSdkMinimumVersion = 10.0,
  LrToolkitIdentifier = 'vn.editer.anh',
  LrPluginName = 'Editer Ảnh',
  LrLibraryMenuItems = {
    { title = 'Editer: 1. Chọn ảnh mẫu mỗi cảnh', file = 'Buoc1.lua' },
    { title = 'Editer: 2. Đồng bộ cả cảnh theo ảnh bạn chỉnh', file = 'Buoc2.lua' },
    { title = 'Editer: Chép màu kiểu Evoto (xuất ảnh mới)…', file = 'ChepMauAnh.lua' },
    { title = 'Editer: Chép màu từ ảnh JPG mẫu (thanh trượt Lightroom)…', file = 'ChepMau.lua' },
    { title = 'Editer: Hướng dẫn', file = 'HuongDan.lua' },
  },
  LrExportMenuItems = {
    { title = 'Editer: 1. Chọn ảnh mẫu mỗi cảnh', file = 'Buoc1.lua' },
    { title = 'Editer: 2. Đồng bộ cả cảnh theo ảnh bạn chỉnh', file = 'Buoc2.lua' },
    { title = 'Editer: Chép màu kiểu Evoto (xuất ảnh mới)…', file = 'ChepMauAnh.lua' },
    { title = 'Editer: Chép màu từ ảnh JPG mẫu (thanh trượt Lightroom)…', file = 'ChepMau.lua' },
    { title = 'Editer: Hướng dẫn', file = 'HuongDan.lua' },
  },
  VERSION = { major = 0, minor = 14, revision = 2, build = 0 },
}
