"""
Package mã nguồn của plugin phân tích nhóm hằng ngày.

Phần triển khai cốt lõi sử dụng kiến trúc DDD:
- application: điều phối domain service và xử lý use case
- domain: logic nghiệp vụ cốt lõi, độc lập với nền tảng
- infrastructure: adapter cho các dịch vụ bên ngoài
- shared: công cụ và hằng số dùng chung giữa các tầng

Các module cũ đang được chuyển đổi dần:
- analysis: triển khai analyzer
- core: thành phần cốt lõi
- reports: tạo báo cáo
- scheduler: tác vụ định kỳ
- utils: hàm tiện ích
- visualization: thành phần trực quan hoá
"""
