/**
 * Global 入口 —— 整个应用只有一个实例，看不到任何播放器窗口，
 * 所以 iina.core / iina.mpv / iina.sidebar 在这里都取不到。
 * 职责限定为跟播放无关的全局事项。
 *
 * 保留这个文件的意义：将来若要由插件自行拉起并回收本机后端进程，逻辑放这里。
 * 已实测支持：utils.exec 派生的后台进程，在 exec 返回之后可以继续存活。
 */

iina.console.log("[bcb] global 入口已加载");
