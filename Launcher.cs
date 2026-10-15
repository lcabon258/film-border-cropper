using System;
using System.IO;
using System.Diagnostics;
using System.Threading;
using System.Web.Script.Serialization;
using System.Collections.Generic;
using System.Windows.Forms;
class FilmBorderCropperLauncher {
 [STAThread] static int Main(string[] args) {
  string root=AppDomain.CurrentDomain.BaseDirectory;
  for(int j=0;j<args.Length;j++) if(args[j]=="--dialog") return Picker(args);
  string workspace=Path.Combine(root,"Projects","MyFilm");
  try { var remembered=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(File.ReadAllText(Path.Combine(root,"last_project.json")));string candidate=Convert.ToString(remembered["workspace"]);if(Directory.Exists(candidate)&&File.Exists(Path.Combine(candidate,"Film_Project.json")))workspace=candidate; } catch {}
  bool choose=false;
  for(int i=0;i<args.Length;i++) { if(args[i]=="--choose-project") choose=true; if(args[i]=="--workspace" && i+1<args.Length) workspace=Path.GetFullPath(args[++i]); }
  if(choose) { using(var dialog=new FolderBrowserDialog()) { dialog.Description="選擇底片專案資料夾；照片副本、專案與輸出會保存在此"; dialog.SelectedPath=Directory.Exists(workspace)?workspace:root; if(dialog.ShowDialog()!=DialogResult.OK)return 0; workspace=dialog.SelectedPath; } }
  try {
   Directory.CreateDirectory(workspace);
   string session=Path.Combine(workspace,"server_session.json");
   if(File.Exists(session)) { try { var data=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(File.ReadAllText(session)); int pid=Convert.ToInt32(data["pid"]); var process=Process.GetProcessById(pid); string url=Convert.ToString(data["url"]); Uri uri=new Uri(url); if(!process.HasExited && uri.Scheme=="http" && uri.Host=="127.0.0.1" && (String.Equals(process.MainModule.FileName,Path.Combine(root,"_internal","runtime","pythonw.exe"),StringComparison.OrdinalIgnoreCase)||String.Equals(process.MainModule.FileName,Path.Combine(root,"_internal","runtime","python.exe"),StringComparison.OrdinalIgnoreCase))) { Process.Start(new ProcessStartInfo(url){UseShellExecute=true});return 0; } } catch {} }
   string python=Path.Combine(root,"_internal","runtime","pythonw.exe");
   string script=Path.Combine(root,"app","server.py");
   if(!File.Exists(python)||!File.Exists(script))throw new Exception("程式檔案不完整。請先將整個 ZIP 解壓縮，不要只複製 EXE。");
   if(File.Exists(session))File.Move(session,session+".previous_"+DateTime.UtcNow.Ticks);
   Process backend=Process.Start(new ProcessStartInfo(python,"\""+script+"\" --workspace \""+workspace.TrimEnd('\\')+"\\.\" --no-browser") { UseShellExecute=false,CreateNoWindow=true,WindowStyle=ProcessWindowStyle.Hidden,WorkingDirectory=root });
   for(int i=0;i<300;i++) {
    if(backend.HasExited)throw new Exception("本機服務未能啟動。請使用「啟動診斷.cmd」查看原因。");
    if(File.Exists(session)) { try { var data=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(File.ReadAllText(session)); string url=Convert.ToString(data["url"]); Uri uri=new Uri(url); if(uri.Scheme!="http"||uri.Host!="127.0.0.1")throw new Exception("啟動網址不正確。");Process.Start(new ProcessStartInfo(url){UseShellExecute=true});return 0; } catch(IOException) {} catch(ArgumentException) {} }
    Thread.Sleep(100);
   }
   throw new Exception("本機服務啟動逾時。請使用「啟動診斷.cmd」查看原因。");
  } catch(Exception e) { try { File.WriteAllText(Path.Combine(root,"launch_error.txt"),DateTime.Now+"\r\n"+e.ToString()); } catch {} MessageBox.Show(e.Message,"Film Border Cropper",MessageBoxButtons.OK,MessageBoxIcon.Information);return 1; }
 }
 static string Argument(string[] args,string key,string fallback) { for(int i=0;i<args.Length-1;i++) if(args[i]==key)return args[i+1]; return fallback; }
 [STAThread] static int Picker(string[] args) {
  string output=Argument(args,"--result-file","");
  var answer=new Dictionary<string,object>();
  try {
   string kind=Argument(args,"--dialog","");string initial=Argument(args,"--initial",AppDomain.CurrentDomain.BaseDirectory);
   if(kind!="open"&&kind!="new")throw new Exception("專案選擇類型無效。");
   using(var owner=new Form()) {
   owner.ShowInTaskbar=false;owner.Width=1;owner.Height=1;owner.Opacity=0;owner.TopMost=true;owner.Show();owner.Activate();
   using(var pick=new FolderBrowserDialog()) {
    pick.Description=kind=="open"?"開啟專案：選擇內含 Film_Project.json 的資料夾":"建立新專案：先選擇來源照片資料夾（包含子資料夾）";
    pick.SelectedPath=Directory.Exists(initial)?initial:AppDomain.CurrentDomain.BaseDirectory;
    if(pick.ShowDialog(owner)!=DialogResult.OK)answer["cancelled"]=true;
    else if(kind=="open") { answer["cancelled"]=false;answer["workspace"]=pick.SelectedPath; }
    else {
     string source=Path.GetFullPath(pick.SelectedPath);string name=new DirectoryInfo(source).Name;
     foreach(char invalid in Path.GetInvalidFileNameChars())name=name.Replace(invalid,'_');if(String.IsNullOrWhiteSpace(name))name="新底片專案";
     using(var form=new Form()) {
      form.Text="建立底片專案";form.Width=640;form.Height=290;form.StartPosition=FormStartPosition.CenterScreen;form.FormBorderStyle=FormBorderStyle.FixedDialog;form.MaximizeBox=false;form.MinimizeBox=false;form.AutoScaleMode=AutoScaleMode.Dpi;form.Font=new System.Drawing.Font("Microsoft JhengHei",10);
      var nameLabel=new Label(){Text="專案名稱",Left=22,Top=24,Width=110};var nameBox=new TextBox(){Text=name,Left=140,Top=20,Width=440};
      var pathLabel=new Label(){Text="保存位置",Left=22,Top=69,Width=110};var pathBox=new TextBox(){Text=Directory.Exists(initial)?initial:AppDomain.CurrentDomain.BaseDirectory,ReadOnly=true,Left=140,Top=65,Width=340};
      var browse=new Button(){Text="選擇…",Left=490,Top=63,Width=90,Height=30};
      var summary=new Label(){Left=22,Top=110,Width=560,Height=55,AutoEllipsis=true};
      Action update=()=>{summary.Text="將建立資料夾："+pathBox.Text+Path.DirectorySeparatorChar+nameBox.Text+"\r\n來源照片保持不變，完整副本放入新專案。";};
      nameBox.TextChanged+=(sender,e)=>update();pathBox.TextChanged+=(sender,e)=>update();
      browse.Click+=(sender,e)=>{using(var folder=new FolderBrowserDialog()){folder.Description="選擇保存位置；會在這裡建立以專案名稱命名的子資料夾";folder.SelectedPath=pathBox.Text;if(folder.ShowDialog(form)==DialogResult.OK)pathBox.Text=folder.SelectedPath;}};
      var create=new Button(){Text="建立專案",Left=365,Top=184,Width=105,Height=34};var cancel=new Button(){Text="取消",Left=480,Top=184,Width=100,Height=34,DialogResult=DialogResult.Cancel};
      create.Click+=(sender,e)=>{string text=nameBox.Text.Trim();if(String.IsNullOrEmpty(text)||text=="."||text==".."||text.EndsWith(".")||text.IndexOfAny(Path.GetInvalidFileNameChars())>=0){MessageBox.Show(form,"請輸入有效的專案名稱。","專案名稱");return;}if(!Directory.Exists(pathBox.Text)){MessageBox.Show(form,"請選擇存在的保存位置。","保存位置");return;}if(Directory.Exists(Path.Combine(pathBox.Text,text))||File.Exists(Path.Combine(pathBox.Text,text))){MessageBox.Show(form,"同名位置已存在，請改名或使用開啟專案。","專案已存在");return;}nameBox.Text=text;form.DialogResult=DialogResult.OK;form.Close();};
      form.Controls.AddRange(new Control[]{nameLabel,nameBox,pathLabel,pathBox,browse,summary,create,cancel});form.AcceptButton=create;form.CancelButton=cancel;update();
      if(form.ShowDialog(owner)!=DialogResult.OK)answer["cancelled"]=true;
      else {answer["cancelled"]=false;answer["source"]=source;answer["name"]=nameBox.Text;answer["workspace"]=Path.Combine(pathBox.Text,nameBox.Text);}
     }
    }
   }
   }
  } catch(Exception e) { answer.Clear();answer["error"]=e.Message; }
  try { if(String.IsNullOrEmpty(output))return 2;File.WriteAllText(output,new JavaScriptSerializer().Serialize(answer),new System.Text.UTF8Encoding(false));return answer.ContainsKey("error")?1:0; } catch { return 2; }
 }

}
