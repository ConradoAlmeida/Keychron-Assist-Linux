%global debug_package %{nil}
%global repoarchive Keychron-Assist-Linux

Name:           kassist
Version:        0.1.0
Release:        1%{?dist}
Summary:        Local helper for the Keychron Launcher (unofficial)

License:        MIT
URL:            https://github.com/ConradoAlmeida/Keychron-Assist-Linux
Source0:        %{url}/archive/v%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  systemd-rpm-macros

Requires:       python3 >= 3.9
Requires:       glib2
Requires:       xdg-utils
Requires:       desktop-file-utils
Recommends:     chromium

%description
kassist serves the app list to the Keychron Launcher website, stores the
Quick Start bindings and opens the bound app when the keyboard key fires,
either through GNOME custom shortcuts or by reading evdev directly.

It runs as a per-user systemd service and listens on 127.0.0.1 only.

%prep
%setup -q -n %{repoarchive}-%{version}

%build
sed 's|@EXEC@|/usr/bin/kassist run|' packaging/kassist.service > kassist.service
sed 's|@EXEC@|/usr/bin/kassist|' packaging/launcherassist.desktop > launcherassist.desktop

%install
install -D -m 0755 kassist.py %{buildroot}%{_bindir}/kassist
install -D -m 0755 packaging/kassist-setup %{buildroot}%{_bindir}/kassist-setup
install -D -m 0644 kassist.service %{buildroot}%{_userunitdir}/kassist.service
install -D -m 0644 launcherassist.desktop %{buildroot}%{_datadir}/applications/launcherassist.desktop
install -D -m 0644 packaging/99-keychron-assist.rules %{buildroot}%{_udevrulesdir}/99-keychron-assist.rules

%files
%{_bindir}/kassist
%{_bindir}/kassist-setup
%{_userunitdir}/kassist.service
%{_datadir}/applications/launcherassist.desktop
%{_udevrulesdir}/99-keychron-assist.rules
%doc README.md SECURITY.md LICENSE

%changelog
* Sat Oct 24 2026 Conrado Almeida <conrado.almeida@outlook.com> - 0.1.0-1
- Initial package.
